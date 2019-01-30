import logging
from typing import Any, Dict, Tuple

from gymwipe.baSimulation.BA import SCHEDULER, PROTOCOL, TIMESLOT_LENGTH
from gymwipe.control.scheduler import RoundRobinTDMAScheduler, MyDQNTDMAScheduler
from gymwipe.envs.core import Interpreter
from gymwipe.networking.devices import NetworkDevice
from gymwipe.networking.mac_layers import (ActuatorMacTDMA, GatewayMac,
                                           SensorMacTDMA, newUniqueMacAddress)
from gymwipe.networking.messages import (Message, Packet, SimpleNetworkHeader,
                                         StackMessageTypes, Transmittable)
from gymwipe.networking.physical import FrequencyBand
from gymwipe.networking.simple_stack import SimpleMac, SimplePhy
from gymwipe.plants.core import OdePlant
from gymwipe.plants.sliding_pendulum import SlidingPendulum
from gymwipe.simtools import SimMan, SimTimePrepender, Notifier

logger = SimTimePrepender(logging.getLogger(__name__))


def generatePlant():
    """
    Generates random ODEPlant
    """


class ComplexNetworkDevice(NetworkDevice):
    """
    A :class:`NetworkDevice` implementation running a network stack that
    consists of a SimplePHY and a SimpleMAC, SensorMAC or ControllerMAC. It offers a method for sending a
    packet using the MAC layer, as well as a callback method that will be
    invoked when a packet is received. Also, receiving can be turned on or of by
    setting :attr:`receiving` either to ``True`` or to ``False``.
    """

    def __init__(self, name: str, xPos: float, yPos: float, frequencyBand: FrequencyBand, type: ""):
        super(ComplexNetworkDevice, self).__init__(name, xPos, yPos, frequencyBand)
        self._receiving = False
        self._receiverProcess = None # a SimPy receiver process

        self.mac: bytes = newUniqueMacAddress()
        """bytes: The address that is used by the MAC layer to identify this device"""

        if type is "Sensor":
            # Initialize PHY and MAC
            self._phy = SimplePhy("phy", self, self.frequencyBand)
            self._mac = SensorMacTDMA("mac", self, self.frequencyBand.spec, self.mac)
        elif type is "Controller":
            # Initialize PHY and MAC
            self._phy = SimplePhy("phy", self, self.frequencyBand)
            self._mac = ActuatorMacTDMA("mac", self, self.frequencyBand.spec, self.mac)
        else:
            # Initialize PHY and MAC
            self._phy = SimplePhy("phy", self, self.frequencyBand)
            self._mac = SimpleMac("mac", self, self.frequencyBand.spec, self.mac)

        # Connect them with each other
        self._mac.ports["phy"].biConnectWith(self._phy.ports["mac"])
    
    # inherit __init__ docstring
    __init__.__doc__ = NetworkDevice.__init__.__doc__

    def mac_address(self):
        return self._mac.addr

    def send(self, data):
        raise NotImplementedError

    def onReceive(self, packet: Packet):
        """
        This method is invoked whenever :attr:`receiving` is ``True`` and a
        packet has been received.

        Note:
            After :attr:`receiving` has been set to ``False`` it might still be
            called within :attr:`RECEIVE_TIMEOUT` seconds.

        Args:
            packet: The packet that has been received
        """
        raise NotImplementedError


class GatewayDevice(NetworkDevice):
    """
    A Radio Resource Management :class:`NetworkDevice` implementation. It runs a
    network stack consisting of a SimplePHY and a GatewayMAC. It offers a
    method for frequency band assignment and operates an
    :class:`~gymwipe.envs.core.Interpreter` instance that provides observations
    and rewards for a learning agent.
    """

    def __init__(self, name: str, xPos: float, yPos: float, frequencyBand: FrequencyBand,
                    deviceIndexToMacDict: Dict[int, bytes], interpreter, control):
        # No type definition for 'interpreter' to avoid circular dependencies
        """
            deviceIndexToMacDict: A dictionary mapping integer indexes to device
                MAC addresses. This allows to pass the device index used by a
                learning agent instead of a MAC address to
                :meth:`assignFrequencyBand`.
            interpreter(:class:`~gymwipe.envs.core.Interpreter`): The
                :class:`~gymwipe.envs.core.Interpreter` instance to be used for
                observation and reward calculations
        """
        self.mac: bytes = newUniqueMacAddress()
        super(GatewayDevice, self).__init__(name, xPos, yPos, frequencyBand)

        self.control = control

        
        self.interpreter = interpreter
        """
        :class:`~gymwipe.envs.core.Interpreter`: The
        :class:`~gymwipe.envs.core.Interpreter` instance that provides
        domain-specific feedback on the consequences of :meth:`assignFrequencyBand`
        calls
        """

        self.deviceIndexToMacDict = deviceIndexToMacDict
        """
        A dictionary mapping integer indexes to device MAC addresses. This
        allows to pass the device index used by a learning agent instead of a
        MAC address to :meth:`assignFrequencyBand`.
        """

        self.macToDeviceIndexDict: Dict[bytes, int] = {mac: index for index, mac in self.deviceIndexToMacDict.items()}
        """
        The counterpart to :attr:`deviceIndexToMacDict`
        """

        # Initialize PHY and MAC
        self._phy = SimplePhy("phy", self, self.frequencyBand)
        self._mac = GatewayMac("mac", self, self.frequencyBand.spec, self.mac)
        # Connect them with each other
        self._mac.ports["phy"].biConnectWith(self._phy.ports["mac"])

        self._receiving = True
        self._receiverProcess = None  # a SimPy receiver process

        # Connect the "upper" mac layer output to the interpreter
        def onPacketReceived(p: Packet):
            # Mapping MAC addresses to indexes
            senderIndex = self.macToDeviceIndexDict[p.header.sourceMAC]
            self.interpreter.onPacketReceived(senderIndex, p.payload)
        self._mac.gates["networkOut"].nReceives.subscribeCallback(onPacketReceived)


    # merge __init__ docstrings
    __init__.__doc__ = NetworkDevice.__init__.__doc__ + __init__.__doc__


class Gateway(GatewayDevice):
    scheduler = None

    def __init__(self, scheduler: str, sensorMACS: [], actuatorMACS: [], name: str, xPos: float, yPos: float,
                 frequencyBand: FrequencyBand, schedule_timeslots: int):

        indexToMAC = {}
        self.sensors = sensorMACS
        self.actuators = actuatorMACS
        self.nextScheduleCreation = 0
        self.schedule_timeslots = schedule_timeslots

        for i in range(len(sensorMACS)):
            indexToMAC[i] = sensorMACS[i]

        for i in range(len(sensorMACS), (len(sensorMACS)+len(actuatorMACS))):
            indexToMAC[i] = actuatorMACS[i-len(sensorMACS)]
        super(Gateway, self).__init__(name, xPos, yPos, frequencyBand, indexToMAC, DQNInterpreter(SCHEDULER), Control())

        self._create(scheduler)
        self._n_schedule_created = Notifier("new Schedule created", self)
        self._n_schedule_created.subscribeCallback(self.interpreter.newSchedule)

        SimMan.process(self._gateway())

    def _create(self, schedule_name: str):
        creator = self.__getattribute__("_create_"+schedule_name)
        creator()

    def _create_roundrobinTDMA(self):
        self.scheduler = RoundRobinTDMAScheduler(list(self.deviceIndexToMacDict.values()), self.sensors, self.actuators,
                                                 self.schedule_timeslots)
        logger.debug("RoundRobinTDMAScheduler created", sender=self)

    def _create_DQNTDMAScheduler(self):
        self.scheduler = MyDQNTDMAScheduler(list(self.deviceIndexToMacDict.values()), self.sensors, self.actuators,
                                            self.schedule_timeslots)
        logger.debug("DQNTDMAScheduler created", sender=self)

    def _send_schedule(self, schedule):
        clock = SimMan.now
        send_cmd = Message(
            StackMessageTypes.SEND, {
                "schedule": schedule,
                "clock": clock
            }
        )
        self._mac.gates["networkIn"].send(send_cmd)
        self.nextScheduleCreation = SimMan.now + schedule.get_end_time()* TIMESLOT_LENGTH
        yield send_cmd.eProcessed

    def _gateway(self):
        if PROTOCOL == 1:  # TDMA
            if SCHEDULER == 1:  # Round Robin
                while True:
                    yield SimMan.timeoutUntil(self.nextScheduleCreation)
                    schedule = self.scheduler.next_schedule()
                    clock = SimMan.now
                    send_cmd = Message(
                        StackMessageTypes.SEND, {
                            "schedule": schedule,
                            "clock": clock
                        }
                    )
                    self._mac.gates["networkIn"].send(send_cmd)
                    self.nextScheduleCreation = SimMan.now + schedule.get_end_time()* TIMESLOT_LENGTH
                    yield send_cmd.eProcessed
                    # TODO: wait for control slot and activate controller

            elif SCHEDULER == 2:
                observation = self.interpreter.getObservation()
                schedule = self.scheduler.next_schedule(observation)
                self._n_schedule_created.trigger(schedule)
                while True:
                    yield SimMan.timeoutUntil(self.nextScheduleCreation)
                    observation = self.interpreter.getObservation()

                    schedule = self.scheduler.next_schedule()
            elif SCHEDULER == 3:
                pass

        elif PROTOCOL == 2:  # CSMA
            pass


class SimpleSensor(ComplexNetworkDevice):
    """
    A sensor that observes the given plant (noise added)
    """
    def __init__(self, name: str, yPos: float, frequencyBand: FrequencyBand,
                    plant: SlidingPendulum, sampleInterval: float):
        super(SimpleSensor, self).__init__(name, plant.getWagonPos(), yPos, frequencyBand, "Sensor")
        self.plant = plant
        self.sampleInterval = sampleInterval
        logger.debug("Sensor initialized", sender=self)
        SimMan.process(self._sensor())

    def _noise(self, state):
        """
        Adds gaussian white noise to an observed state
        """
        # TODO: add noise
        return state

    def send(self, data):
        """
            Sends the last observed state to the mac layer
        """
        send_cmd = Message(StackMessageTypes.SEND, {"state": data})
        self._mac.gates["networkIn"].send(send_cmd)

    def _sensor(self):

        while True:
            self.position.x = self.plant.getWagonPos()
            self.state = self._noise(self.plant.getAngle())
            logger.debug("State sampled: " + self.state.__str__(), sender=self)
            self.send(self.state)
            yield SimMan.timeout(self.sampleInterval)

    def onReceive(self, packet: Packet):
        pass


class SimpleActuator(ComplexNetworkDevice):
    def __init__(self, name: str, yPos: float, frequencyBand: FrequencyBand, plant: SlidingPendulum):
        super(SimpleActuator, self).__init__(name, plant.getWagonPos, yPos, frequencyBand, "Actuator")
        self.plant = plant

        SimMan.process(self._positionUpdater())

    def _positionUpdater(self):
        while True:
            self.position.x = self.plant.getWagonPos()
            yield SimMan.timeout(1e-3)  # 1 ms

    def send(self, data):
        pass

    def onReceive(self, packet: Packet):
        self.plant.setMotorVelocity(packet.payload.value)


class Control:
    def __init__(self):
        name = "bla"


class DQNInterpreter(Interpreter):

    def __init__(self, scheduler):
        self.scheduler = scheduler
        self.gateway = None # set after gateway creation

    def onPacketReceived(self, senderIndex: int, receiverIndex: int, payload: Transmittable):
        if self.scheduler == 2:
            pass
        elif self.scheduler == 3:
            pass

    def onFrequencyBandAssignment(self, deviceIndex: int, duration: int):
        pass

    def getReward(self):
        """
        Reward is
        """
        if self.scheduler == 2:
            pass
        elif self.scheduler == 3:
            pass
        return None

    def getObservation(self):
        if self.scheduler == 2:
            pass
        elif self.scheduler == 3:
            pass
        return None

    def getDone(self):
        return False

    def getInfo(self):
        pass

    def newSchedule(self, schedule):
        pass
