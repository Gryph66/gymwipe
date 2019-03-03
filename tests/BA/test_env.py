from gymwipe.baSimulation import BAEnvironment
import pytest
import logging

from gymwipe.baSimulation.constants import Configuration, SchedulerType, ProtocolType
from gymwipe.simtools import SimMan

from ..fixtures import simman


def test_env_creation(caplog):
    # caplog.set_level(logging.DEBUG, logger='gymwipe.baSimulation.BAEnvironment')
    # caplog.set_level(logging.DEBUG, logger='gymwipe.plants.state_space_plants')
    # caplog.set_level(logging.DEBUG, logger='gymwipe.networking.MyDevices')
    # caplog.set_level(logging.DEBUG, logger='gymwipe.networking.mac_layers')

    # caplog.set_level(logging.DEBUG, logger='gymwipe.networking.simple_stack')
    # caplog.set_level(logging.DEBUG, logger='gymwipe.control.paper_scheduler')
    # caplog.set_level(logging.DEBUG, logger='gymwipe.control.scheduler')

    roundrobin_config = [Configuration(SchedulerType.ROUNDROBIN,
                                       ProtocolType.TDMA,
                                       timeslot_length=0.01,
                                       episodes=200,
                                       horizon=500,
                                       plant_sample_time=0.005,
                                       sensor_sample_time=0.01,
                                       num_plants=2,
                                       num_instable_plants=0,
                                       schedule_length=2,
                                       show_error_rates=False,
                                       show_inputs_and_outputs=False,
                                       kalman_reset=True,
                                       seed=42)]

    random_config = [Configuration(SchedulerType.RANDOM,
                                   ProtocolType.TDMA,
                                   timeslot_length=0.01,
                                   episodes=190,
                                   horizon=500,
                                   plant_sample_time=0.005,
                                   sensor_sample_time=0.01,
                                   num_plants=2,
                                   num_instable_plants=0,
                                   schedule_length=2,
                                   show_error_rates=False,
                                   show_inputs_and_outputs=False,
                                   kalman_reset=False,
                                   seed=42),
                     Configuration(SchedulerType.RANDOM,
                                   ProtocolType.TDMA,
                                   timeslot_length=0.01,
                                   episodes=190,
                                   horizon=500,
                                   plant_sample_time=0.005,
                                   sensor_sample_time=0.01,
                                   num_plants=2,
                                   num_instable_plants=0,
                                   schedule_length=3,
                                   show_error_rates=False,
                                   show_inputs_and_outputs=False,
                                   kalman_reset=False,
                                   seed=42),
                     Configuration(SchedulerType.RANDOM,
                                   ProtocolType.TDMA,
                                   timeslot_length=0.01,
                                   episodes=190,
                                   horizon=500,
                                   plant_sample_time=0.005,
                                   sensor_sample_time=0.01,
                                   num_plants=2,
                                   num_instable_plants=0,
                                   schedule_length=4,
                                   show_error_rates=False,
                                   show_inputs_and_outputs=False,
                                   kalman_reset=False,
                                   seed=42)
                     ]

    configs = [Configuration(SchedulerType.ROUNDROBIN,
                             ProtocolType.TDMA,
                             timeslot_length=0.01,
                             episodes=190,
                             horizon=500,
                             plant_sample_time=0.005,
                             sensor_sample_time=0.01,
                             num_plants=2,
                             num_instable_plants=0,
                             schedule_length=2,
                             show_error_rates=False,
                             show_inputs_and_outputs=False,
                             kalman_reset=False,
                             seed=42),
               Configuration(SchedulerType.ROUNDROBIN,
                             ProtocolType.TDMA,
                             timeslot_length=0.01,
                             episodes=190,
                             horizon=500,
                             plant_sample_time=0.005,
                             sensor_sample_time=0.01,
                             num_plants=2,
                             num_instable_plants=0,
                             schedule_length=3,
                             show_error_rates=False,
                             show_inputs_and_outputs=False,
                             kalman_reset=False,
                             seed=42),
               Configuration(SchedulerType.ROUNDROBIN,
                             ProtocolType.TDMA,
                             timeslot_length=0.01,
                             episodes=190,
                             horizon=500,
                             plant_sample_time=0.005,
                             sensor_sample_time=0.01,
                             num_plants=2,
                             num_instable_plants=0,
                             schedule_length=4,
                             show_error_rates=False,
                             show_inputs_and_outputs=False,
                             kalman_reset=False,
                             seed=42),
               Configuration(SchedulerType.DQN,
                             ProtocolType.TDMA,
                             timeslot_length=0.01,
                             episodes=190,
                             horizon=500,
                             plant_sample_time=0.005,
                             sensor_sample_time=0.01,
                             num_plants=2,
                             num_instable_plants=0,
                             schedule_length=2,
                             show_error_rates=False,
                             show_inputs_and_outputs=False,
                             kalman_reset=False,
                             seed=42),
               Configuration(SchedulerType.DQN,
                             ProtocolType.TDMA,
                             timeslot_length=0.01,
                             episodes=190,
                             horizon=500,
                             plant_sample_time=0.005,
                             sensor_sample_time=0.01,
                             num_plants=2,
                             num_instable_plants=0,
                             schedule_length=3,
                             show_error_rates=False,
                             show_inputs_and_outputs=False,
                             kalman_reset=False,
                             seed=40),
               Configuration(SchedulerType.DQN,
                             ProtocolType.TDMA,
                             timeslot_length=0.01,
                             episodes=190,
                             horizon=500,
                             plant_sample_time=0.005,
                             sensor_sample_time=0.01,
                             num_plants=2,
                             num_instable_plants=0,
                             schedule_length=4,
                             show_error_rates=False,
                             show_inputs_and_outputs=False,
                             kalman_reset=False,
                             seed=40)
               ]

    used_configs = random_config + roundrobin_config
    for i in range(len(used_configs)):
        config = used_configs[i]
        BAEnvironment.initialize(config)
        while not BAEnvironment.is_done:
            SimMan.runSimulation(0.01)
        BAEnvironment.reset_env()
