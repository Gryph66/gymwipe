"""
Gym environments based on the Simple network devices
"""

from typing import Dict, List

import gym
import numpy as np
from gym import error, spaces, utils
from gym.utils import seeding

from gymwipe.envs.core import BaseEnv, Interpreter
from gymwipe.networking.attenuation_models import FsplAttenuation
from gymwipe.networking.devices import SimpleNetworkDevice, SimpleRrmDevice
from gymwipe.networking.messages import (FakeTransmittable, IntTransmittable,
                                         Packet, Transmittable)
from gymwipe.networking.physical import Channel
from gymwipe.simtools import SimMan


class CounterTrafficEnv(BaseEnv):
    """
    An environment for testing reinforcement learning with three devices:

        * Two network devices that send a configurable amount of data to each other
        * A simple RRM operating an interpreter for that use case

    Optimally, a learning agent will fit the length of the assignment intervals
    to the amount of data sent by the devices.
    """

    COUNTER_INTERVAL = 0.001

    COUNTER_BYTE_LENGTH = 2

    COUNTER_BOUND = 2 ** (8*COUNTER_BYTE_LENGTH)

    class SenderDevice(SimpleNetworkDevice):
        """
        A device sending packets with increasing COUNTER_BYTE_LENGTH-byte
        integers. Every `COUNTER_INTERVAL` seconds, a packet with the current
        integer is sent `packetMultiplicity` times.
        """
        
        def __init__(self, name: str, xPos: float, yPos: float, channel: Channel,
                        packetMultiplicity: int):
            super(CounterTrafficEnv.SenderDevice, self).__init__(name, xPos, yPos, channel)
            self.packetMultiplicity = packetMultiplicity
            self.counter = 1
            SimMan.process(self.senderProcess())

            self.destinationMac: bytes = None # to be set after construction
        
        def senderProcess(self):
            assert self.destinationMac is not None
            while True:
                for _ in range(self.packetMultiplicity):
                    data = IntTransmittable(CounterTrafficEnv.COUNTER_BYTE_LENGTH, self.counter)
                    self.send(data, self.destinationMac)
                if self.counter < CounterTrafficEnv.COUNTER_BOUND:
                    self.counter += 1
                yield SimMan.timeout(CounterTrafficEnv.COUNTER_INTERVAL)
    
    class CounterTrafficInterpreter(Interpreter):

        def __init__(self, env: "CounterTrafficEnv"):
            self._env = env
            self.reset()
        
        def reset(self):
            self._latestDifference = 0
            self._lastRewardDifference = 0
            self.receivedValues = [0 for _ in range(len(self._env.senders))]
            self._done = False

        def onPacketReceived(self, senderIndex: int, receiverIndex: int,  payload: Transmittable):
            value = payload.value
            self.receivedValues[senderIndex] = value
            self._latestDifference = self.receivedValues[0] - self.receivedValues[1]
            if value == self._env.COUNTER_BOUND:
                self._done = True
    
        def onChannelAssignment(self, deviceIndex: int, duration: int):
            self._lastAssignDeviceIndex = deviceIndex

        def getReward(self):
            """
            Reward is the change of the difference between the values received
            from both devices: Positive if the difference became smaller,
            negative otherwise
            """
            if self._lastAssignDeviceIndex == 0:
                return 0.0
            absDifference = abs(self._latestDifference)
            reward = self._lastRewardDifference - absDifference
            self._lastRewardDifference = absDifference
            return float(reward)

        def getObservation(self):
            return self._latestDifference + self._env.COUNTER_BOUND
        
        def getDone(self):
            return self._done
        
        def getInfo(self):
            # DQN in keras-rl crashes when the values are iterable, thus the
            # string below
            return {"Latest received values": str(self.receivedValues)}

    def __init__(self):
        channel = Channel([FsplAttenuation])
        super(CounterTrafficEnv, self).__init__(channel, deviceCount=2)

        # The difference between the lastly received values from both devices
        # summed up with the COUNTER_BOUND will be the observation.
        self.observation_space = spaces.Discrete(2 * CounterTrafficEnv.COUNTER_BOUND)

        SimMan.initEnvironment()

        self.senders: List[self.SenderDevice] = [
            CounterTrafficEnv.SenderDevice("Sender 1", 0, 2, self.channel, 1),
            CounterTrafficEnv.SenderDevice("Sender 2", 0, -2, self.channel, 3)
        ]
        self.deviceIndexToMacDict: Dict[int, bytes] = {i: s.mac for i, s in enumerate(self.senders)}
        self.senders[0].destinationMac = self.senders[1].mac
        self.senders[1].destinationMac = self.senders[0].mac

        interpreter = self.CounterTrafficInterpreter(self)
        self.rrm = SimpleRrmDevice("RRM", 0, 0, self.channel, self.deviceIndexToMacDict, interpreter)

    def reset(self):
        """
        Resets the state of the environment and returns an initial observation.
        """
        for sender in self.senders:
            sender.counter = 0
        
        self.rrm.interpreter.reset()
        
        return self.rrm.interpreter.getObservation()
    
    def step(self, action):
        assert self.action_space.contains(action)
        deviceIndex = action["device"]
        duration = action["duration"]*self.ASSIGNMENT_DURATION_FACTOR

        # Assign the channel
        assignSignal = self.rrm.assignChannel(deviceIndex, duration)

        # Run the simulation until the assignment ends
        SimMan.runSimulation(assignSignal.eProcessed)

        # Return (observation, reward, done, info)
        return self.rrm.interpreter.getFeedback()

    def render(self, mode='human', close=False):
        values = self.rrm.interpreter.receivedValues
        print("Last Received: {}, difference: {:6d}".format(values, values[1]-values[0]))
