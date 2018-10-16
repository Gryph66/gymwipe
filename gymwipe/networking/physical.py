"""
Physical layer related components
"""
import functools
import logging
from abc import ABC, abstractmethod, abstractproperty
from fractions import Fraction
from math import e, exp, log10, pi, sqrt
from typing import Any, Dict, List, Tuple, Type, TypeVar

from scipy.special import binom
from simpy import Event

import gymwipe.devices as devices
from gymwipe.devices import Device
from gymwipe.networking.messages import Packet
from gymwipe.simtools import Notifier, SimMan, SimTimePrepender

logger = SimTimePrepender(logging.getLogger(__name__))

# Helper functions for physical calculations

def calculateEbToN0Ratio(signalPower: float, noisePower: float, bitRate: int,
                            returnDb: bool = False) -> float:
    """
    Computes :math:`E_b/N_0 = \\frac{S}{N_0 R}` (the "ratio of signal energy per
    bit to noise power density per Hertz" :cite:`stallings2005data`) given the
    signal power :math:`S_{dBm}`, the noise power :math:`N_{0_{dBm}}`, and the
    bit rate :math:`R`, according to p. 95 of :cite:`stallings2005data`.

    Args:
        signalPower: The signal power :math:`S` in dBm
        noisePower: The noise power :math:`N_0` in dBm
        bitRate: The bit rate :math:`R` in bps
        returnDb: If set to ``True``, the ratio will be returned in db.
    """
    s_dbw = signalPower + 30
    n_dbw = noisePower + 30
    ratio_db = s_dbw - n_dbw - 10*log10(bitRate)
    if returnDb:
        return ratio_db
    return 10**(ratio_db/10)

sqrtOfTwoPi = sqrt(2*pi)

def approxQFunction(x: float) -> float:
    """
    Approximates the complementary error function

    :math:`Q(x)=\\frac{1}{\\sqrt{2\\pi}} \\int_{x}^{\\infty} exp
    \\big(-\\frac{u^2}{2} \\big) du` (see :cite:`sklar1993defining`)

    for :math:`x > 3`. The following formula, taken from
    :cite:`sklar1993defining`, is used:

    :math:`Q(x) \\cong \\frac{1}{x\\sqrt{2\\pi}} exp \\big( -\\frac{x^2}{2}
    \\big)`
    """
    assert x > 3
    return 1/(x*sqrtOfTwoPi) * exp(-(x^2 / 2))

class Mcs(ABC):
    """
    The :class:`Mcs` class represents a Modulation and Coding Scheme. As the MCS
    (beside channel characteristics) determines the relation between
    Signal-to-Noise Ratio (SNR) and the resulting Bit Error Rate (BER), it
    offers a :meth:`getBitErrorRateBySnr` method that is used by receiving PHY
    layer instances.

    Currently, only BPSK modulation is implemented (see :class:`DpskMcs` for details).
    Subclass :class:`Mcs` if you need something more advanced.
    """

    _codeRateToMaxCorrectableBer = {}

    @abstractmethod
    def calculateBitErrorRate(self, signalPower: float, noisePower: float, bitRate: float) -> float:
        """
        Computes the bit error rate for the passed parameters if this modulation
        and coding scheme is used.

        Args:
            signalPower: The signal power :math:`S` in dBm
            noisePower: The noise power :math:`N_0` in dBm
            bitRate: The bit rate :math:`R` in bps

        Returns: The estimated resulting bit error rate (a float in [0,1])
        """
    
    # Transmissions need Mcs objects (also for length determination)
    # Also: Bitrate needs a proper unit!
    
    @abstractproperty
    def codeRate(self) -> Fraction:
        """
        Fraction: The relative amount of transmitted bits that are not used for
        forward error correction
        """

    def maxCorrectableBer(self) -> float:
        """
        Returns the maximum bit error rate that can be handled when using the
        MCS. It depends on the codeRate and is calculated via the
        Varshamov-Gilbert bound.
        """
        # check for cached result
        if self.codeRate in Mcs._codeRateToMaxCorrectableBer:
            return Mcs._codeRateToMaxCorrectableBer[self.codeRate]

        k = self.codeRate.numerator
        n = self.codeRate.denominator
        bound = 2**(n-k)

        # find max. t with sum of binomial coefficients less or equal than bound
        currentSum = 0
        t = 0
        while currentSum <= bound:
            currentSum += binom(n, t)
            t += 1
        t -= 1
        
        # up to t errors can be corrected in a block of k bits
        maxBer = float(t)/k
        Mcs._codeRateToMaxCorrectableBer[self.codeRate] = maxBer
        return maxBer

class BpskMcs(Mcs):
    """
    A Binary Phase-Shift-Keying MCS
    """

    def __init__(self, codeRate: Fraction):
        self.codeRate = codeRate

    def calculateBitErrorRate(self, signalPower: float, noisePower: float, bitRate: float) -> float:
        ratio = calculateEbToN0Ratio(signalPower, noisePower, bitRate)
        return approxQFunction(sqrt(2*ratio))

class Transmission:
    """
    A :class:`Transmission` models the process of a device sending a specific
    packet via a communication channel.

    Note:
        The proper way to instantiate :class:`Transmission` objects is via
        :meth:`Channel.transmit`.
    """

    def __init__(self, sender: Device, mcs: Mcs, power: float, bitrateHeader: int, bitratePayload: int, packet: Packet, startTime: float):
        self.sender: Device = sender
        """Device: The device that initiated the transmission"""

        self.power: float = power
        """float: The tramsmission power in dBm"""

        self.mcs: Mcs = mcs
        """Mcs: The modulation and coding scheme used for the transmission"""

        self.bitrateHeader: int = bitrateHeader
        """int: The header's bitrate in bps"""

        self.bitratePayload: int = bitratePayload
        """int: The payload's bitrate in bps"""

        self.packet: Packet = packet
        """Packet: The packet sent in the transmission"""

        self.startTime: float = startTime
        """float: The simulated time at which the transmission started"""

        self.duration = packet.header.byteSize() * 8 / bitrateHeader + packet.payload.byteSize() * 8 / bitratePayload
        """float: The time in seconds taken by the transmission"""
        
        self.stopTime = startTime + self.duration
        """
        float: The last moment in simulated time at which the transmission is
        active
        """

        # create the completesEvent
        self.completes: Event = SimMan.timeoutUntil(self.stopTime)
        """
        :class:`~simpy.events.Event`: A SimPy event that is triggered at
        :attr:`stopTime`.
        """
        
    def __str__(self):
        return "Transmission(from: {}, power: {}, duration: {})".format(self.sender, self.power, self.duration)

class ChannelSpec:
    """
    A channel specification stores a channel's frequency and its bandwidth.
    """

    def __init__(self, frequency: float = 2.4e9, bandwidth: float = 22e6):
        """
        Args:
            frequency: The channel's frequency in Hz. Defaults to 2.4 GHz.
            bandwidth: The channel's bandwidth in Hz. Defaults to 22 MHz (as in
                IEEE 802.11)
        """
        self.frequency = frequency
        self.bandwidth = bandwidth

class AttenuationModel():
    """
    An :class:`AttenuationModel` calculates the attenuation (measured in db) of
    any signal sent from one network device to another. It runs a SimPy process
    and subscribes to the positionChanged events of the :class:`NetworkDevice`
    instances it belongs to. When the attenuation value changes, the
    :attr:`attenuationChanged` event succeeds.
    """

    def __init__(self, channelSpec: ChannelSpec, deviceA: Device, deviceB: Device):
        """
        Args:
            channelSpec: The channel specification of the corresponding channel
            deviceA: Network device a
            deviceB: Network device b
        """
        self.channelSpec = channelSpec
        self.devices: Tuple[Device] = (deviceA, deviceB)
        self.attenuation: float = 0
        """
        float: The attenuation of any signal sent from :class:`NetworkDevice`
        `deviceA` to :class:`NetworkDevice` `deviceB` (or vice versa) at the
        currently simulated time, measured in db.
        """

        self.nAttenuationChanges: Notifier = Notifier("Attenuation changes", self)
        """
        :class:`gymwipe.simtools.Notifier`: A notifier that is triggered when
        the attenuation value changes, providing the new attenuation value.
        """
    
    def _setAttenuation(self, newAttenuation: float):
        """
        Updates :attr:`attenuation` to `newAttenuation` if they
        differ and triggers :attr:`nAttenuationChanges`.
        """
        if newAttenuation != self.attenuation:
            self.attenuation = newAttenuation
            self.nAttenuationChanges.trigger(newAttenuation)

class BaseAttenuationModel(AttenuationModel):
    """
    An :class:`AttenuationModel` subclass that executes :meth:`_positionChanged`
    whenever one of its two devices changes its position and the distance
    between the devices does not exceed :attr:`STANDBY_THRESHOLD`.
    """

    STANDBY_THRESHOLD: float = 30
    """
    float: The minimum distance in metres, that allows the
    :class:`AttenuationModel` not to react on position changes of its devices
    """

    def __init__(self, channelSpec: ChannelSpec, deviceA: Device, deviceB: Device):
        super(BaseAttenuationModel, self).__init__(channelSpec, deviceA, deviceB)

        def positionChangedCallback(p: devices.Position):
            distance = self.devices[0].position.distanceTo(self.devices[1].position)
            if distance < self.STANDBY_THRESHOLD:
                self._positionChanged(p.owner)
        for device in self.devices:
            device.position.nChange.subscribeCallback(positionChangedCallback)
    
    def _positionChanged(self, device: Device):
        """
        This method is called whenever the position of either deviceA or deviceB
        changes and the distance between the devices does not exceed
        :attr:`STANDBY_THRESHOLD`.

        Args:
            device: The device of which the position has changed.
        """
        pass


AttenuationModelClass = TypeVar('AttenuationModel', bound=AttenuationModel)

class JoinedAttenuationModel(AttenuationModel):
    """
    An :class:`AttenuationModel` that adds the attenuation values of two or more
    given :class:`AttenuationModel` instances. If the position of one of both
    devices is changed, it will gather the Test update notifications of its
    :class:`AttenuationModel` instances, sum them up and trigger the
    :attr:`nAttenuationChanges` notifier only once after the updates (this is
    implemented using callback priorities). When an :class:`AttenuationModel`
    instance changes its attenuation without reacting to a position update, the
    :attr:`nAttenuationChanges` notifier of the :class:`JoinedAttenuationModel`
    will be triggered as a direct consequence.
    """

    def __init__(self, channelSpec: ChannelSpec, deviceA: Device, deviceB: Device, models: List[Type[AttenuationModelClass]]):
        """
        Args:
            channelSpec: The channel specification of the corresponding channel
            deviceA: Network device a
            deviceB: Network device b
            models: A non-empty list of the :class:`AttenuationModel` subclasses
                to create a :class:`JoinedAttenuationModel` instance of
        """
        # instantiate models
        self._models = [model(channelSpec, deviceA, deviceB) for model in models]
        self._modelAttenuations = {}

        for model in self._models:
            self._modelAttenuations[model] = model.currentAttenuation
            # define a callback for updating the model's
            # attenuation value as it changes
            def updater(newAttenuation: float):
                self._modelAttenuations[model] = newAttenuation
                if not self._updateGatheringActive:
                    # update the sum
                    self._updateSum()
            model.nAttenuationChanges.addCallback(updater)
        
        # Setting up callbacks to gather updates that happen as a consequence to
        # position changes
        self._updateGatheringActive = False

        # Before models execute updates:
        def beforeUpdates(value: Any):
            self._updateGatheringActive = True
        # After models have executed updates:
        def afterUpdates(value: Any):
            self._updateSum()
            self._updateGatheringActive = False
        
        for device in self.devices:
            device.position.nChange.subscribeCallback(beforeUpdates, priority=1)
            device.position.nChange.subscribeCallback(afterUpdates, priority=-1)
    
    def _updateSum(self):
        self._setAttenuation(sum(self._modelAttenuations.values()))

class AttenuationModelFactory():
    """
    A factory for :class:`AttenuationModel` instances.
    """

    def __init__(self, channelSpec: "ChannelSpec", models: List[AttenuationModelClass]):
        """
        Args:
            channelSpec: The channel specification of the corresponding channel
            models: A non-empty list of :class:`AttenuationModel` subclasses
                that will be used for instantiating attenuation models.
        """
        self._channelSpec = channelSpec
        self._models = models
        self._instances = {}
    
    def getInstance(self, deviceA: Device, deviceB: Device) -> AttenuationModel:
        """
        Returns the :class:`AttenuationModel` for signals sent from `deviceA` to
        `deviceB` and vice versa. If not yet existent, a new
        :class:`AttenuationModel` instance will be created. If the factory was
        initialized with multiple :class:`AttenuationModel` subclasses, a
        :class:`JoinedAttenuationModel` will be handed out.
        """
        key = frozenset((deviceA, deviceB))
        if key in self._instances:
            return self._instances.get(key)
        else:
            # initializing a new instance
            if len(self._models) == 1:
                instance = self._models[0](self._channelSpec, deviceA, deviceB)
            else:
                instance = JoinedAttenuationModel(self._channelSpec, deviceA, deviceB, self._models)
            self._instances[key] = instance
            return instance

class Channel:
    """
    The Channel class serves as a manager for transmission objects and
    represents a physical channel. It also offers the
    :meth:`getAttenuationModel` method that returns an AttenuationModel for any
    pair of devices. 
    """

    def __init__(self, modelClasses: List[AttenuationModelClass], frequency: float = 2.4e9, bandwidth: float = 22e6):
        """
        Args:
            modelClasses: A non-empty list :class:`AttenuationModel` subclasses
                that will be used for attenuation calculations regarding this
                channel.
            frequency: The channel's frequency in Hz. Defaults to 2.4 GHz.
            bandwidth: The channel's bandwidth in Hz. Defaults to 22 MHz (as in
                IEEE 802.11)
        """

        self.spec = ChannelSpec(frequency, bandwidth)
        """
        :class:`ChannelSpec`: The channel's specification object
        """

        # The isinstance check below would always return false - why?
        #for a in args:
        #    assert isinstance(a, AttenuationModel)
        self._attenuationModelFactory = AttenuationModelFactory(self.spec, modelClasses)
        self._transmissions: List[Transmission] = []
        self._transmissionInReachNotifiers: Dict[Tuple[Device, float], Notifier] = {}

        self.nNewTransmission: Notifier = Notifier("New transmission", self)
        """
        :class:`~gymwipe.simtools.Notifier`: A notifier that is triggered when
        :meth:`transmit` is executed, providing the :class:`Transmission` object
        representing the transmission.
        """

    def getAttenuationModel(self, deviceA: Device, deviceB: Device) -> AttenuationModel:
        """
        Returns the AttenuationModel instance that provides attenuation values
        for transmissions between `deviceA` and `deviceB`.
        """
        return self._attenuationModelFactory.getInstance(deviceA, deviceB)

    def transmit(self, sender: Device, mcs: Mcs, power: float, brHeader: int, brPayload: int, packet: Packet) -> Transmission:
        """
        Simulates the transmission of `packet` with the given properties. This
        is achieved by creating a :class:`Transmission` object with the values
        passed and triggering the :attr:`transmissionStarted` event of the
        :class:`Channel`.

        Args:
            sender: The device that transmits
            mcs: The modulation and coding scheme to be used (represented by an
                instance of an Mcs subclass)
            power: Transmission power in dBm
            brHeader: Header bitrate
            brPayload: Payload bitrate
            packet: :class:`~gymwipe.networking.messages.Packet` object
                representing the packet being transmitted

        Returns:
            The :class:`Transmission` object representing the transmission
        """
        t = Transmission(sender, mcs, power, brHeader, brPayload, packet, SimMan.now)
        self._transmissions.append((t, t.startTime, t.stopTime))
        logger.debug("%s added to channel", t)
        self.nNewTransmission.trigger(t)
        # check which transmissionInReachNotifiers have to be triggered
        for (receiver, radius), notifier in self._transmissionInReachNotifiers.items():
            if receiver.position.distanceTo(sender.position) <= radius:
                notifier.trigger(t)
        return t
    
    def getTransmissions(self, fromTime: int, toTime: int) -> List[Tuple[Transmission, int, int]]:
        """
        Returns the transmissions that were active within the timely interval of
        [`fromTime`,`toTime`].

        Args:
            fromTime: The number of the first time step of the interval to
                return transmissions for
            toTime: The number of the last time step of the interval to return
                transmissions for
        
        Returns:
            A list of tuples, one for each :class:`Transmission`, each
            consisting of the :class:`Transmission` object, the transmission's
            start time, and stop time.
        """
        return [(t, a, b) for (t, a, b) in self._transmissions
                    if a <= fromTime <= toTime <= b
                    or fromTime <= a <= toTime
                    or fromTime <= b <= toTime]
    
    def getActiveTransmissions(self) -> List[Transmission]:
        """
        Returns a list of transmissions that are currently active.
        """
        now = SimMan.now
        return [t for (t, a, b) in self._transmissions if a <= now <= b]
    
    def getActiveTransmissionsInReach(self, receiver: Device, radius: float) -> List[Transmission]:
        """
        Returns a list of transmissions that are currently active and whose
        sender is positioned within the radius specified by `radius` around the
        receiver.
        
        Args:
            receiver: The :class:`NetworkDevice`, around which the radius is
                considered
            radius: The radius around the receiver (in metres)
        """
        return [t for t in self.getActiveTransmissions() if t.sender.position.distanceTo(receiver) <= radius]
    
    def nNewTransmissionInReach(self, receiver: Device, radius: float) -> Notifier:
        """
        Returns a notifier that is triggered iff a new :class:`Transmission`
        starts whose sender is positioned within the radius specified by
        `radius` around the `receiver`.

        Args:
            receiver: The :class:`NetworkDevice`, around which the radius is
                considered
            radius: The radius around the receiver (in metres)
        """

        if (receiver, radius) in self._transmissionInReachNotifiers:
            return self._transmissionInReachNotifiers[receiver, radius]
        # creating a new notifier otherwise
        n = Notifier("New Transmission within radius {:d} around {}".format(radius, receiver), self)
        self._transmissionInReachNotifiers[receiver, radius] = n
        return n
