"""
Physical layer related components
"""
import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Tuple, Type, TypeVar

from simpy import Event

import gymwipe.devices as devices
from gymwipe.networking.core import NetworkDevice
from gymwipe.networking.messages import Packet
from gymwipe.simtools import Notifier, SimMan, SimTimePrepender

logger = SimTimePrepender(logging.getLogger(__name__))

class Transmission:
    """
    A :class:`Transmission` models the process of a device sending a specific
    packet via a communication channel.

    Note:
        The proper way to instantiate :class:`Transmission` objects is via
        :meth:`Channel.transmit`.
    """

    def __init__(self, sender: NetworkDevice, power: float, bitrateHeader: int, bitratePayload: int, packet: Packet, startTime: int):
        self._sender = sender
        self._power = power
        self._bitrateHeader = bitrateHeader
        self._bitratePayload = bitratePayload
        self._packet = packet
        self._startTime = startTime

        # calculate duration
        self._duration = packet.header.byteSize() * 8 / bitrateHeader + packet.payload.byteSize() * 8 / bitratePayload
        self._stopTime = startTime + self._duration
        # create the completesEvent
        self._completesEvent = SimMan.timeoutUntil(self._stopTime)
    
    def __str__(self):
        return "Transmission(from: {}, power: {}, duration: {})".format(self.sender, self.power, self.duration)
    
    @property
    def startTime(self):
        """int: The time at which the transmission started"""
        return self._startTime
    
    @property
    def power(self):
        """float: The tramsmission power"""
        return self._power
    
    @property
    def bitrateHeader(self):
        """int: The header's bitrate given in bits / time unit"""
        return self._bitrateHeader
    
    @property
    def bitratePayload(self):
        """int: The payload's bitrate given in bits / time unit"""
        return self._bitratePayload
    
    @property
    def sender(self):
        """NetworkDevice: The device that initiated the transmission"""
        return self._sender
    
    @property
    def packet(self):
        """Packet: The packet sent in the transmission"""
        return self._packet
    
    @property
    def duration(self):
        """float: The number of time units taken by the transmission"""
        return self._duration
    
    @property
    def stopTime(self):
        """
        float: The last moment in simulated time at which the transmission is
        active
        """
        return self._stopTime

    @property
    def completes(self):
        """
        :class:`~simpy.events.Event`: A SimPy event that is triggered at
        :attr:`stopTime`.
        """
        return self._completesEvent

class AttenuationModel():
    """
    An :class:`AttenuationModel` calculates the attenuation (measured in db) of
    any signal sent from one network device to another. It runs a SimPy process
    and subscribes to the positionChanged events of the :class:`NetworkDevice`
    instances it belongs to. When the attenuation value changes, the
    :attr:`attenuationChanged` event succeeds.
    """

    def __init__(self, deviceA: NetworkDevice, deviceB: NetworkDevice):
        """
        Args:
            deviceA: Network device a
            deviceB: Network device b
        """
        self.devices: Tuple[NetworkDevice] = (deviceA, deviceB)
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

    def __init__(self, deviceA: NetworkDevice, deviceB: NetworkDevice):
        super(BaseAttenuationModel, self).__init__(deviceA, deviceB)

        def positionChangedCallback(p: devices.Position):
            distance = self.devices[0].position.distanceTo(self.devices[1].position)
            if distance < self.STANDBY_THRESHOLD:
                self._positionChanged(p.owner)
        for device in self.devices:
            device.position.nChange.subscribeCallback(positionChangedCallback)
    
    def _positionChanged(self, device: NetworkDevice):
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

    def __init__(self, deviceA: NetworkDevice, deviceB: NetworkDevice, models: List[Type[AttenuationModelClass]]):
        """
        Args:
            deviceA: Network device a
            deviceB: Network device b
            models: The :class:`AttenuationModel` subclasses to create a
                :class:`JoinedAttenuation` instance of
        """
        #super(JoinedAttenuation, self).__init__()
        # instantiate models
        self._models = [model(deviceA, deviceB) for model in models]
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
    A factory for :class:`AttenuationModel` instances. It is instantiated
    providing the :class:`AttenuationModel` subclass(es) that will be applied.
    """

    def __init__(self, *args):
        self._models = args
        self._instances = {}
    
    def getInstance(self, deviceA: NetworkDevice, deviceB: NetworkDevice) -> AttenuationModel:
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
                instance = self._models[0](deviceA, deviceB)
            else:
                instance = JoinedAttenuationModel(deviceA, deviceB, self._models)
            self._instances[key] = instance
            return instance

class Channel:
    """
    The Channel class serves as a manager for transmission objects and
    represents a physical channel. It also holds the corresponding
    AttenuationModel instance.
    """

    def __init__(self, attenuationModelFactory: AttenuationModelFactory):
        self._attenuationModelFactory: AttenuationModelFactory = attenuationModelFactory
        self._transmissions: List[Transmission] = []
        self._transmissionInReachNotifiers: Dict[Tuple[NetworkDevice, float], Notifier] = {}

        self.nNewTransmission: Notifier = Notifier("New transmission", self)
        """
        :class:`~gymwipe.simtools.Notifier`: A notifier that is triggered when
        :meth:`transmit` is executed, providing the :class:`Transmission` object
        representing the transmission.
        """
    
    def getAttenuationModel(self, deviceA: NetworkDevice, deviceB: NetworkDevice) -> AttenuationModel:
        """
        Returns the AttenuationModel instance that provides attenuation values
        for transmissions between `deviceA` and `deviceB`.
        """
        return self._attenuationModelFactory.getInstance(deviceA, deviceB)

    def transmit(self, sender: NetworkDevice, power: float, brHeader: int, brPayload: int, packet: Packet) -> Transmission:
        """
        Creates a :class:`Transmission` object with the values passed and stores
        it. Also triggers the :attr:`~Channel.transmissionStarted` event of the
        :class:`Channel`.

        Args:
            sender: The NetworkDevice that transmits
            power: Transmission power [dBm]
            brHeader: Header bitrate
            brPayload: Payload bitrate
            packet: :class:`~gymwipe.networking.messages.Packet` object
                representing the packet being transmitted

        Returns:
            The :class:`Transmission` object representing the transmission
        """
        t = Transmission(sender, power, brHeader, brPayload, packet, SimMan.now)
        self._transmissions.append((t, t.startTime, t.stopTime))
        logger.debug("Transmission %s added to channel", t)
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
    
    def getActiveTransmissionsInReach(self, receiver: NetworkDevice, radius: float) -> List[Transmission]:
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
    
    def nNewTransmissionInReach(self, receiver: NetworkDevice, radius: float) -> Notifier:
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
