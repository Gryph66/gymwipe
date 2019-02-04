import itertools
import logging
import random
from collections import deque
from enum import Enum

from gymwipe.baSimulation.BA import EPISODES, T, reset
import numpy as np
import tensorflow as tf
from keras.layers import Dense
from keras.models import Sequential
from keras.optimizers import Adam

from gymwipe.simtools import SimMan, SimTimePrepender
import time
logger = SimTimePrepender(logging.getLogger(__name__))


class Schedule:
    """
        A framework for schedule classes. A implemented schedule will produce and contain the specific schedule for a scheduling descision taken by a scheduler
    """
    def __init__(self, action):
        self.action = action # Action output from Scheduler
        self.schedule = []
        self.string = ""

    def get_string(self):
        raise NotImplementedError

    def get_end_time(self):
        raise NotImplementedError


class Scheduler:
    """
        A framework for a Scheduler class, which will produce channel allocation schedules
    """
    def __init__(self, devices, timeslots: int):
        """
        Args:
            devices: a list of MAC adresses which should be considered while producing a schedule
            int timeslots: the number of timeslots for which scheduling decisions should be taken
        """
        self.devices = devices  # list of sensor/controller mac addresses
        self.schedule = None  # current schedule
        self.timeslots = timeslots  # length of schedule

    def next_schedule(self, observation, last_reward) -> Schedule:
        """
            produces the next schedule, possibly given information about the system's state. Raises a
            NotImplementedError if not overridden by a subclass

            Args:
                observation: a representation of the observed state
                last_reward: the reward for the previous produced schedule
        """
        raise NotImplementedError


class RoundRobinTDMAScheduler(Scheduler):
    """
    A Round Robin Scheduler producing a TDMA Schedule
    """
    def __init__(self, devices: [], sensors: [], actuators: [], timeslots: int):
        super(RoundRobinTDMAScheduler, self).__init__(devices, timeslots)
        self.sensors = sensors
        self.actuators = actuators
        self.nextDevice = 0 # position in device list of the first device in the next schedule
        self.wasActuator = False
        
    def next_schedule(self, observation=None, last_reward=None):
        action = []
        for i in range(self.timeslots):
            if self.devices[self.nextDevice] in self.actuators:
                if self.wasActuator:
                    action.append([self.devices[self.nextDevice], 0])
                    self.wasActuator = False
                else:   
                    action.append([self.devices[self.nextDevice], 1])
                    self.wasActuator = True
            else:
                action.append([self.devices[self.nextDevice], 0])
            if not self.wasActuator:
                if self.nextDevice == (len(self.devices) - 1):
                    self.nextDevice = 0
                else:
                    self.nextDevice += 1
            
        logger.debug("new schedule generated", sender=self)    
        self.schedule = TDMASchedule(action)
        return self.schedule

    def get_next_control_slot(self, last_control_slot) -> [int, str]:
        schedule_list = self.schedule.string.split(" ")
        for i in range(len(schedule_list)):
            if ((i % 4) - 1) == 0:  # is mac address
                if schedule_list[i] in self.actuators:  # is control line
                    if schedule_list[i-1] > last_control_slot:  # is next control line
                        return [schedule_list[i-1], schedule_list[i]]
        return None


class PaperDQNTDMAScheduler(Scheduler):
    def __init__(self, devices: {}, sensors: [], actuators: [], timeslots: int, learning: bool):
        super(PaperDQNTDMAScheduler, self).__init__(devices, timeslots)
        self.sensors = sensors
        self.actuators = actuators
        self.last_observation = None
        self.last_action = None
        self.learning = learning

        self.state_dim = 3 * len(self.sensors) + 2 * len(self.actuators) + self.timeslots
        self.action_set = list(itertools.combinations_with_replacement(self.devices, self.timeslots))
        self.action_size = len(self.action_set)

        self.batch_size = 32  # mini-batch size
        self.memory = deque(maxlen=20000)  # replay memory
        self.alpha = 0.95  # discount rate
        self.epsilon = 1  # exploration rate
        self.epsilon_min = 0.01
        self.epsilon_decay = 0.999
        self.learning_rate = np.exp(-4)
        self.c = 100  # how many steps to fix target Q
        self.model = self._build_model()
        self.target_model = self._build_model()
        self.update_target_model()
        self.episode = 0
        self.t = 0
        self.evaluator = None # has to be set after creation

    def next_schedule(self, observation, last_reward):

        if self.learning:
            if self.episode <= EPISODES:
                if self.t == 0:
                    start_time = time.time()
                    ave_cost = 0
                    ave_reward = 0
                    discard = 0
                if self.t <= T:
                    if self.t != 0:
                        self.remember(self.last_observation, self.last_action, last_reward, observation)
                        ave_reward += last_reward
                        ave_cost += -1 * last_reward
                    self.last_observation = np.reshape(observation, [1, self.state_dim])
                    action = self.act(self.last_observation)
                    self.last_action = action

                    if np.mod(self.t, self.c) == 0:
                        self.update_target_model()

                    if len(self.memory) > self.batch_size:
                        self.replay()
                    self.t += 1
                    if last_reward < -1e4:
                        discard = 1
                        logger.debug("discarded learning: episode %d, t %d", self.episode, self.t, sender=self)
                        self.t = T+1

                else:
                    if discard == 0:
                        ave_cost /= T
                        ave_reward /= T
                        end_time = time.time()
                        self.evaluator.episode_results(ave_cost, ave_reward, self.episode, start_time-end_time)
                    self.episode += 1
                    self.t = 0
                    reset()
            return TDMASchedule(self.last_action)
        else:
            # TODO: send simulation results to evaluator
            return TDMASchedule(self.act(np.reshape(observation, [1, self.state_dim])))

    def _build_model(self):
        # Neural Net for Deep-Q learning Model
        model = Sequential()
        model.add(Dense(1024, input_dim=self.state_dim, activation='relu'))
        #        model.add(Dropout(.4))
        model.add(Dense(1024, activation='relu'))
        model.add(Dense(self.action_size, activation='linear'))
        model.compile(loss='mean_squared_error', optimizer=Adam(lr=self.learning_rate, decay=.001))
        #        model.compile(loss='mean_squared_error', optimizer=Adam(lr=self.learning_rate))
        return model

    def update_target_model(self):
        # copy weights from model to target_model
        self.target_model.set_weights(self.model.get_weights())

    def remember(self, state, action, reward, next_state):
        self.memory.append((state, action, reward, next_state))

    def act(self, state):
        if np.random.rand() <= self.epsilon:
            return random.randrange(self.action_size)
        act_values = self.model.predict(state)
        return np.argmax(act_values[0])  # returns action

    def replay(self):
        minibatch = random.sample(self.memory, self.batch_size)
        states = list()
        targets = list()
        for state, action, reward, next_state in minibatch:
            target = self.model.predict(state)
            target_Q = self.target_model.predict(next_state)[0]  # [0] for row matrix to vector
            target[0][action] = reward + self.alpha * np.max(target_Q)
            states.append(state[0])
            targets.append(target[0])
        self.model.fit(np.reshape(states, [self.batch_size, self.state_dim]),
                       np.reshape(targets, [self.batch_size, self.action_size]), epochs=1, verbose=0)
        if self.epsilon > self.epsilon_min:
            self.epsilon *= self.epsilon_decay

    def load(self, name):
        self.model.load_weights(name)

    def save(self, name):
        self.model.save_weights(name)


class MyDQNTDMAScheduler(Scheduler):
    """
        A DQN Scheduler producing a TDMA Schedule
    """
    def __init__(self, devices: {}, sensors: [], actuators: [], timeslots: int):
        super(MyDQNTDMAScheduler, self).__init__(devices, timeslots)
        self.sensors = sensors
        self.actuators = actuators

        self.batch_size = 32  # mini-batch size
        self.memory = deque(maxlen=20000)   # replay memory
        self.alpha = 0.95              # discount rate
        self.epsilon = 1                  # exploration rate
        self.epsilon_min = 0.01
        self.epsilon_decay = 0.999
        self.learning_rate = np.exp(-4)
        self.c = 100        # how many steps to fix target Q
        
        self.input_size = 3 * len(self.sensors) + 2 * len(self.actuators) + self.timeslots
        self.action_set = list(itertools.combinations_with_replacement(self.devices, self.timeslots))
        self.action_size = len(self.action_set)
        self.string = ', '.join(map(str, self.action_set))
        logger.debug("action set: " + self.string, sender=self)

        self.model = self._build_model()
        self.targetModel = self._build_model()
        logger.debug("initialized. state size : " + self.input_size.__str__() + " action size: " + self.action_size.__str__(), sender=self)

    def _build_model(self):
        model = Sequential()
        model.add(Dense(1024, input_dim=self.input_size, activation='relu'))
        model.add(Dense(1024, activation='relu'))
        model.add(Dense(self.action_size, activation='linear'))
        model.compile(loss='mean_squared_error', optimizer=Adam(lr=self.learning_rate, decay=.001))

        return model

    def update_target_model(self):
        # copy weights from model to target_model
        self.targetModel.set_weights(self.model.get_weights())

    def remember(self, state, action, reward, next_state):
        self.memory.append((state, action, reward, next_state))

    def load(self, name):
        self.model.load_weights(name)

    def save(self, name):
        self.model.save_weights(name)

    def act(self, state):
        if np.random.rand() <= self.epsilon:
            return random.randrange(self.action_size)
        act_values = self.model.predict(state)
        return np.argmax(act_values[0])  # returns action

    def next_schedule(self, observation, last_reward):
        return None


class MyDQNCSMAScheduler(Scheduler):
    """
        A DQN Scheduler producing a CSMA Schedule
    """
    def __init__(self, devices, timeslots: int):
        super(MyDQNCSMAScheduler, self).__init__(devices, timeslots)
        self. result = 0

    def next_schedule(self, observation, last_reward):
        x = tf.Variable(3, name ="x")
        y = tf.Variable(4, name = "y")

        f = x*x*y +y +2
        with tf.Session() as sess:
            x.initializer.run()
            y.initializer.run()
            self.result = f.eval()

        logger.debug("Computation result:" + self.result.__str__(), sender = self)    
        return None


class TDMASchedule(Schedule):
    """
        A TDMA Schedule implementation. In every timeslot one single device will be allowed to send. 
        If multiple consecutive timeslots are assigned to the same device, 
        the device won't be written down a second time but the time in the next line will be increased 
        by the amount of the consecutive timeslots
    """
    def __init__(self, action):
        super(TDMASchedule, self).__init__(action)
        last_action = None
        for i in range(len(self.action)):            
            if self.action[i] != last_action:
                self.schedule.append((i+1).__str__() + " " + self.action[i][0].__str__() + " "+
                                     self.action[i][1].__str__() + " 1")
            last_action = self.action[i]
        self.schedule.append((len(action)+1).__str__())
        self.string = " ".join(self.schedule)
        logger.debug("TDMA Schedule created. Content: " + self.string, sender=self)

    def get_string(self):
        return self.string

    def get_next_relevant_timespan(self, mac_address: str, last_step):
        logger.debug("called function with address %s and last step %d", mac_address, last_step, sender=self)
        schedule_list = self.string.split(" ")
        string = "".join(schedule_list)
        logger.debug("schedule list: %s", string, sender=self)
        for i in range(len(schedule_list)):
            if ((i % 4) - 1) == 0:  # is mac adress
                logger.debug("Found a mac address field, address is: %s", schedule_list[i], sender=self)
                if schedule_list[i] == mac_address:
                    logger.debug("mac addresses are the same : %s at timestep %s", mac_address, schedule_list[i-1], sender=self)
                    if int(schedule_list[i-1]) > last_step:
                        logger.debug("relevant span for %s is %s to %s", mac_address, schedule_list[i - 1],
                                     schedule_list[i+3], sender=self)
                        return [int(schedule_list[i-1]), int(schedule_list[i+3])]
        return None

    def get_end_time(self) -> int:
        schedule_list = self.string.split(" ")
        logger.debug("endtime is %s", schedule_list[len(schedule_list)-1], sender=self)
        return int(schedule_list[len(schedule_list)-1])


class CSMASchedule(Schedule):
    """
    A CSMA schedule implementation. Each device is assigned a likelihood of starting to send its data when it is
    currently not receiving data.

    """
    def __init__(self, action, length):
        """

        :param action: The action chosen by the scheduler. Must have the following format:
        [(MAC1, p1), (MAC2, p2) ... (MACn, pn)]
        :param length:  The amount of timeslots in which this schedule is valid
        """
        super(CSMASchedule, self).__init__(action)
        self.length = length
        for i in range(len(self.action)):
            self.schedule.append(self.action[i][0].__str__() + " " + self.action[i][1].__str__())
        self.schedule.append(self.length.__str__())

        self.string = " ".join(self.schedule)
        logger.debug("CSMA Schedule created. Content: " + self.string, sender=self)

    def get_string(self):
        return self.string

    def get_end_time(self):
        return self.length


def csma_encode(schedule: CSMASchedule) -> int:
    bytesize = 0
    for i in range(len(schedule.schedule)-1):
        bytesize += 7
    bytesize += 1
    return bytesize


def tdma_encode(schedule: TDMASchedule, compressed: bool) -> int:
    """
    Computes the length in bytes of the given schedule. If the compressed option is set to True, a compression
    of the schedule is simulated.
    :param schedule: The schedule whose length is to be calculated.
    :param compressed: Determines, if the schedule should be compressed or not
    :return: The length of the schedule in number of bytes
    """
    bytesize = 1  # time byte at the end of the schedule
    if not compressed:
        for i in range((len(schedule.schedule)-1)):
            bytesize += 7
            # TODO: Change when schedule format is fixed
        return bytesize
    else:
        already_in = []
        already_in_time = []
        for i in range((len(schedule.action))):
            if schedule.action[i][0] in already_in:
                bytesize += 3
                logger.debug("mac already in Schedule: %s", schedule.action[i][0], sender="TDMAEncode")
            else:
                logger.debug("mac not yet in schedule: %s", schedule.action[i][0], sender="TDMAEncode")
                bytesize += 7
                already_in.append(schedule.action[i][0])
                already_in_time.append(i+1)
        return bytesize
