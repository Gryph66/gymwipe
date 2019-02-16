import logging
import numpy as np
from gymwipe.baSimulation import constants as c
from gymwipe.networking.attenuation_models import FsplAttenuation
from gymwipe.networking.physical import FrequencyBand
from gymwipe.plants.state_space_plants import StateSpacePlant
from gymwipe.simtools import SimTimePrepender
from gymwipe.networking.MyDevices import SimpleSensor, SimpleActuator, Gateway

logger = SimTimePrepender(logging.getLogger(__name__))

plants = []
controllers = []
sensors = []
sensormacs = []
actuators = []
actuatormacs = []
gateway = None


def initialize():
    frequency_band = FrequencyBand([FsplAttenuation])
    for i in range(c.NUM_PLANTS):
        np.random.seed(c.POSITION_SEED)
        plant = StateSpacePlant(2, 1, c.PLANT_SAMPLE_TIME)
        plants.append(plant)
        controller = plant.generate_controller()
        controllers.append(controller)
        sensor = SimpleSensor("Sensor " + i.__str__(), round(np.random.uniform(0.0, 5.0), 2),
                              round(np.random.uniform(0.0, 5.0), 2),
                              frequency_band, plant)
        sensors.append(sensor)
        sensormacs.append(sensor.mac)
        actuator = SimpleActuator("Actuator" + i.__str__(), round(np.random.uniform(0.0, 5.0), 2),
                                  round(np.random.uniform(0.0, 5.0), 2),
                                  frequency_band, plant)
        actuators.append(actuator)
        actuatormacs.append(actuator.mac)
    gateway = Gateway(sensormacs, actuatormacs, controllers, plants, "Gateway", round(np.random.uniform(0.0, 5.0), 2),
                      round(np.random.uniform(0.0, 5.0), 2), frequency_band, c.SCHEDULE_LENGTH)




class Evaluator:
    def __init__(self, gateway_mac, sensor_macs:[], actuator_macs: []):
        pass

    def schedule_results(self):
        pass

    def episode_results(self, ave_cost, ave_reward, episode, duration):
        pass


def reset():
    pass


if __name__ == "__main__":
    pass