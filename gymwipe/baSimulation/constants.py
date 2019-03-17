from enum import Enum


class SchedulerType(Enum):
    """
    An enumeration of scheduler types to be used for the environment's configuration
    """
    RANDOM = 0
    ROUNDROBIN = 1
    MYDQN = 2
    DQN = 3
    GREEDYWAIT = 4
    GREEDYERROR = 5


class ProtocolType(Enum):
    """
    An enumeration of protocol types to be used for the environment's configuration
    """
    TDMA = 0
    CSMA = 1


TIMESLOT_LENGTH = 0.01
EPISODES = 1  # number of episodes
T = 100  # horizon length
SEED = 40
SCHEDULER = SchedulerType.ROUNDROBIN
PROTOCOL = ProtocolType.TDMA

PLANT_SAMPLE_TIME = 0.01
SENSOR_SAMPLE_TIME = 0.01
SAMPLE_TO_TIMESLOT_RATIO = TIMESLOT_LENGTH/PLANT_SAMPLE_TIME
NUM_PLANTS = 5
INSTABLE_PLANTS = 0
SCHEDULE_LENGTH = 2


class Configuration:
    def __init__(self,
                 scheduler_type: SchedulerType,
                 protocol_type: ProtocolType,
                 timeslot_length: float = 0.01,
                 episodes: int = 200,
                 horizon: int = 500,
                 plant_sample_time: float = 0.02,
                 sensor_sample_time: float = 0.02,
                 num_plants: int = 2,
                 num_instable_plants: int = 0,
                 schedule_length: int = 2,
                 show_inputs_and_outputs: bool = True,
                 show_error_rates: bool = True,
                 kalman_reset: bool = True,
                 show_statistics: bool = True,
                 show_assigned_p_values: bool = True,
                 train: bool = True,
                 simulate: bool = True,
                 simulation_horizon: int = 100,
                 seed: int = 42):
        self.scheduler_type = scheduler_type
        self.protocol_type = protocol_type
        self.timeslot_length = timeslot_length
        self.episodes = episodes
        self.horizon = horizon
        self.train = train
        if protocol_type == ProtocolType.TDMA:
            self.plant_sample_time = schedule_length * timeslot_length
        elif protocol_type == protocol_type.CSMA:
            self.plant_sample_time = plant_sample_time

        self.sample_to_timeslot_ratio = self.timeslot_length*schedule_length/self.plant_sample_time
        self.sensor_sample_time = sensor_sample_time
        self.num_plants = num_plants
        self.num_instable_plants = num_instable_plants
        self.schedule_length = schedule_length
        self.show_inputs_and_outputs = show_inputs_and_outputs
        self.show_error_rates = show_error_rates
        self.kalman_reset = kalman_reset
        self.simulate = simulate
        self.simulation_horizon = simulation_horizon
        self.show_statistics = show_statistics
        self.show_assigned_p_values = show_assigned_p_values
        self.seed = seed



