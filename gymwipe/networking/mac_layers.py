""" 
    Contains different MAC layer implementations
"""
import logging

from simpy.events import Event

from gymwipe.control.scheduler import tdma_encode
from gymwipe.devices import Device
from gymwipe.networking.construction import GateListener, Module
from gymwipe.networking.mac_headers import NCSMacHeader
from gymwipe.networking.messages import (Message, Packet, StackMessageTypes,
                                         Transmittable)
from gymwipe.networking.physical import BpskMcs, FrequencyBandSpec
from gymwipe.simtools import Notifier, SimMan, SimTimePrepender

from gymwipe.baSimulation.BA import TIMESLOT_LENGTH

logger = SimTimePrepender(logging.getLogger(__name__))

macCounter = 0 #global mac counter


"""
float: The length of one time slot in seconds (used for simulating slotted time)
"""

def newUniqueMacAddress() -> bytes:
        """
        A method for generating unique 6-byte-long MAC addresses (currently counting upwards starting at 1)
        """
        global macCounter
        macCounter += 1
        addr = bytearray(6)
        addr[5] = macCounter
        logger.debug("New mac requested")
        return bytes(addr)



class SensorMacTDMA(Module):
    """
        A sensor's mac layer implementation using a :class:`~gymwipe.control.scheduler.TDMASchedule` object.
        It sends its most recent sensordata if the next timeslot is reserved according to the schedule.

    """

    @GateListener.setup
    def __init__(self, name: str, device: Device,frequencyBandSpec: FrequencyBandSpec, addr: bytes):
        super(SensorMacTDMA, self).__init__(name, owner=device)
        self._addPort("phy")
        self._addPort("network")
        self.addr = addr
        self._schedule = None
        self._lastDatapacket = None
        self._nScheduleAdded = Notifier("new schedule received", self)
        self._nScheduleAdded.subscribeProcess(self._senddata, queued=False)
        self._transmissionPower = 0.0 # dBm
        self._mcs = BpskMcs(frequencyBandSpec)
        self._receiving = True
        self.gateway_address = None
        self._network_cmd: Message = None
        """
        The most recent clock signal sent by the gateway. Is sent within a scheduling Packet.
        """
        self._lastGatewayClock = 0
        logger.debug("Initialization completed, MAC address: %s", self.addr, sender=self)

    @GateListener("phyIn", Packet)
    def phyInGateListener(self, packet: Packet):

        if self._receiving is True:
            logger.debug("received a packet from phy, am in receiving mode", sender=self)
            header = packet.header
            if not isinstance(header, NCSMacHeader):
                raise ValueError("Can only deal with header of type NCSMacHeader. Got %s.", type(header), sender=self)
            if header.type[0] == 0: # received schedule from gateway
                self._schedule = packet.payload.value
                self._lastGatewayClock = packet.trailer.value
                self.gateway_address = header.sourceMAC
                self._nScheduleAdded.trigger(packet)
                schedulestr = self._schedule.get_string()
                logger.debug("received a schedule: gateway clock: %s schedule: %s", self._lastGatewayClock.__str__(),
                             schedulestr, sender=self)
            else:
                pass
        else:
            logger.debug("received a packet from phy, but not in receiving mode. packet ignored", sender=self)
            pass

    @GateListener("networkIn", (Message, Packet), queued=False)
    def networkInGateListener(self, cmd):
        if isinstance(cmd, Message):
            if cmd.type is StackMessageTypes.SEND:
                self._network_cmd = cmd
                data = cmd.args["state"]
                sensorsendingtype = bytearray(1)
                sensorsendingtype[0] = 1
                datapacket = Packet(
                    NCSMacHeader(bytes(sensorsendingtype), self.addr),
                    Transmittable(data)
                )
                self._lastDatapacket = datapacket
                logger.debug("%s received new sensordata: %s , saved it", self, data)

    def _stopReceiving(self):
        logger.debug("%s: Stopping to receive.", self)
        self._receiving = False

    def _start_receiving(self):
        logger.debug("%s: Starting to receive.", self)
        self._receiving = True

    def _senddata(self, packet: Packet):
        self._stopReceiving()
        relevant_span = self._schedule.get_next_relevant_timespan(self.addr.__str__(), 0)
        while relevant_span is not None:
            logger.debug("%s: next relevant timespan is [%d, %d]", self, relevant_span[0], relevant_span[1])
            for i in range(relevant_span[0], relevant_span[1]):
                yield SimMan.timeoutUntil(self._lastGatewayClock + i*TIMESLOT_LENGTH)
                send_cmd = Message(
                    StackMessageTypes.SEND, {
                        "packet": self._lastDatapacket,
                        "power": self._transmissionPower,
                        "mcs": self._mcs
                    }
                )
                self.gates["phyOut"].send(send_cmd)
                logger.debug("Transmitting data. Schedule timestep %d", i, sender=self)
                yield send_cmd.eProcessed
                self._network_cmd.setProcessed()
            relevant_span = self._schedule.get_next_relevant_timespan(self.addr, relevant_span[1])
        self._start_receiving()


class ActuatorMacTDMA(Module):
    """
        An actuator's mac layer implementation using a :class:`~gymwipe.control.scheduler.TDMASchedule` object.
    """

    @GateListener.setup
    def __init__(self, name: str, device: Device, frequencyBandSpec: FrequencyBandSpec, addr: bytes):
        super(ActuatorMacTDMA, self).__init__(name , owner=device)
        self._addPort("phy")
        self._addPort("network")
        self.addr = addr
        self._lastDatapacket = None
        self._packetAddedEvent = Event(SimMan.env)
        self._mcs = BpskMcs(frequencyBandSpec)
        self._transmissionPower = 0.0 # dBm
        self._receiving = False
        self._receiveCmd = None
        self._receiveTimeout = None
        logger.debug("Initialization completed, MAC address: %s", self.addr, sender=self)

    @GateListener("phyIn", Packet, queued=True)
    def phyInGateListener(self, packet: Packet):
        header = packet.header
        if not isinstance(header, NCSMacHeader):
            raise ValueError("Can only deal with header of type NCSMacHeader. Got %s.", type(header), sender=self)
        if header.type[0] == 0: # received schedule from gateway
            self.schedule = packet.payload.value
            self.gatewayAdress = header.sourceMAC
            logger.debug("received a schedule", sender=self)
        if header.type[0] == 1:  # received sensor data
            if header.destMAC == self.addr:  # sensor data is for me
                logger.debug("received relevant sensor data", sender=self)
                self.gates["networkOut"].send(packet.payload)

    @GateListener("networkIn",(Message, Packet), queued = True)
    def networkInGateListener(self, cmd):
        if isinstance(cmd, Message):
            if cmd.type is StackMessageTypes.RECEIVE:
                logger.debug("%s: Entering receive mode.", self)
                # start receiving
                self._receiveCmd = cmd
                # set _receiving and a timeout event
                self._receiving = True
                self._receiveTimeout = SimMan.timeout(cmd.args["duration"])
                self._receiveTimeout.callbacks.append(self._receiveTimeoutCallback)

    def _receiveTimeoutCallback(self, event: Event):
        if event is self._receiveTimeout:
            # the current receive message has timed out
            logger.debug("%s: Receive timed out.", self)
            self._receiveCmd.setProcessed()
            self._stopReceiving()
    
    def _stopReceiving(self):
        logger.debug("%s: Stopping to receive.", self)
        self._receiveCmd = None
        self._receiving = False
        self._receiveTimeout = None


class GatewayMac(Module):
    """
        A gateway's mac layer implementation
    """

    @GateListener.setup
    def __init__(self, name: str, device: Device, frequencyBandSpec: FrequencyBandSpec, addr: bytes):
        super(GatewayMac, self).__init__(name, owner=device)
        self._addPort("phy")
        self._addPort("network")
        self.addr = addr

        self._mcs = BpskMcs(frequencyBandSpec)
        self._transmissionPower = 0.0 # dBm
        self._nControlReceived = Notifier("New control message received", self)
        self._nControlReceived.subscribeProcess(self._sendControl, queued= True)
        self._nAnnouncementReceived = Notifier("new schedule message received", self)
        self._nAnnouncementReceived.subscribeProcess(self._sendAnnouncement, queued=True)
        self._receivingMode = False
        logger.debug("Initialization completed, MAC address: %s", self.addr, sender=self)

    @GateListener("phyIn", Packet, queued=True)
    def phyInGateListener(self, packet: Packet):
        logger.debug("%s: received a packet", self)
        header = packet.header
        if not isinstance(header, NCSMacHeader):
            raise ValueError("Can only deal with header of type NCSMacHeader. Got %s.", type(header), sender=self)
        message_type = header.type[0]
        if message_type == 1: #received sensordata
            logger.debug("received sensordata from %s. data is %s", header.sourceMAC, packet.payload.value, sender=self)
        if message_type == 2: #received Actuator ACK
            pass

    @GateListener("networkIn", Message)
    def networkInGateListener(self, message: Message):
        if message.type is StackMessageTypes.SEND:
            logger.debug("%s: Got the schedule %s.", self, message)
            self._nAnnouncementReceived.trigger(message)

        if message.type is StackMessageTypes.SENDCONTROL:
            logger.debug("%s: Got the control message %s.", self, message)
            self._nControlReceived.trigger(message)

    def _sendControl(self, message: Message):
        """
        Is executed by the `_nAnnouncementReceived` notifier in a blocking and
        queued way for every control Packet that is received on the `networkIn`
        gate.
        """
        control = message.args["control"]
        receiver = message.args["receiver"]
        type = bytearray(1)
        type[0] = 2
        controlpacket = Packet(
            NCSMacHeader(bytes(type), self.addr, receiver),
            Transmittable(control)
        )
        sendCmd = Message(
            StackMessageTypes.SEND, {
                "packet": controlpacket,
                "power": self._transmissionPower,
                "mcs": self._mcs
            }
        )
        logger.debug("%s: Sending control: %s", self, controlpacket)
        self.gates["phyOut"].send(sendCmd)
        yield sendCmd.eProcessed
        message.setProcessed()

    def _sendAnnouncement(self, message: Message):
        """
        Is executed by the `_nAnnouncementReceived` notifier in a blocking and
        queued way for every schedule that is received on the `networkIn`
        gate.
        """
        schedule = message.args["schedule"]
        clock = message.args["clock"]
        type = bytearray(1)
        type[0] = 0 # schedule
        announcement = Packet(
            NCSMacHeader(bytes(type), self.addr),
            Transmittable(schedule, tdma_encode(schedule, False)),
            Transmittable(clock)
        )
        sendCmd = Message(
            StackMessageTypes.SEND, {
                "packet": announcement,
                "power": self._transmissionPower,
                "mcs": self._mcs
            }
        )
        logger.debug("%s: Sending schedule: %s", self, announcement.payload.value.get_string())
        self.gates["phyOut"].send(sendCmd)
        yield sendCmd.eProcessed
        message.setProcessed()
