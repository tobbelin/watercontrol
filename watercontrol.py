import time
import os
import RPi.GPIO as GPIO
import logging
from logging.handlers import SysLogHandler
import sqlite3

logger = logging.getLogger()
logger.setLevel(logging.DEBUG)

syslog_handler = SysLogHandler(address='/dev/log')  # Use '/var/run/syslog' for macOS
formatter = logging.Formatter('%(name)s: %(levelname)s %(message)s')
syslog_handler.setFormatter(formatter)
logger.addHandler(syslog_handler)
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(formatter)
logger.addHandler(stream_handler)


# Propagate messages to root logger
logger.propagate = True



from dotenv import load_dotenv
from paho.mqtt.client import Client, MQTTMessage
from ha_mqtt_discoverable import Settings, DeviceInfo
from ha_mqtt_discoverable.sensors import Switch, SwitchInfo, BinarySensor, BinarySensorInfo, Sensor, SensorInfo, DeviceTriggerInfo
import sqlite3

# Load configuration from .env file
load_dotenv()

GPIO_MAIN_WATER = 27
GPIO_AUTOMATIC_WATER = 22
GPIO_WATER_SENSOR = 17



class Database:
    def initialize_db(self):
        cursor = self.conn.cursor()
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS watercontrol (
            id INTEGER PRIMARY KEY,
            name TEXT,
            value FLOAT
        )
        ''')
        cursor.execute('''
        INSERT INTO watercontrol (id, name, value) 
        VALUES (1, 'total_water', 0)
        ON CONFLICT(id) DO NOTHING
        ''')
        self.conn.commit()

    def load_accumulated_value(self):
        cursor = self.conn.cursor()
        cursor.execute('SELECT value FROM watercontrol WHERE id = 1')
        result = cursor.fetchone()
        return result[0] if result else 0

    def save_accumulated_value(self, value):
        cursor = self.conn.cursor()
        cursor.execute('''
        UPDATE watercontrol
        SET value = ?
        WHERE id = 1
        ''', (value,))
        self.conn.commit()

    def __init__(self):
        self.conn = sqlite3.connect('watercontrol.db')
        self.initialize_db()

    # Load, accumulate, and save the value

def setupSafeGpios():
    GPIO.setmode(GPIO.BCM)  # or GPIO.BOARD depending on your pin numbering preference
    GPIO.setup(GPIO_MAIN_WATER, GPIO.OUT, initial=GPIO.LOW)
    GPIO.setup(GPIO_AUTOMATIC_WATER, GPIO.OUT, initial=GPIO.LOW)

class WaterControl:

    # MQTT broker settings from environment variables
    BROKER = os.getenv('MQTT_BROKER', 'localhost')
    PORT = int(os.getenv('MQTT_PORT', 1883))
    USERNAME = os.getenv('MQTT_USERNAME')
    PASSWORD = os.getenv('MQTT_PASSWORD')
    NAME = os.getenv('NAME', 'Water Control')
    IDENTIFIER = os.getenv('IDENTIFIER', 'water_control') # Must be unique for each device
    main_water_switch = None
    automatic_watering_switch = None
    current_water_sensor = None
    mqtt_settings = None
    database = Database()

    current_water_usage = 0.0
    current_water_counter = 0
    total_water_usage = None 

    main_time = 0
    automatic_time = 0

    def setup_mqtt_client(self):
        self.mqtt_settings = Settings.MQTT(host=self.BROKER, port=self.PORT)

    # Define and publish device configuration
    def setup_device(self):
        if self.mqtt_settings is None:
            logger.error("MQTT settings not initialized")
            raise Exception("MQTT settings not initialized")

        device_info = DeviceInfo(name=self.NAME, identifiers=[self.IDENTIFIER], manufacturer="Linghammar", model="Water Control")
        main_water_switch_info = SwitchInfo(device=device_info, name="Main Water", unique_id="main_water_switch")
        watering_switch_info = SwitchInfo(device=device_info, name="Automatic watering", unique_id="watering_switch")
        total_water_sensor_info = SensorInfo(device=device_info, name="Total water used", unique_id="total_water_sensor", device_class="water", unit_of_measurement="l")
        current_water_sensor_info = SensorInfo(device=device_info, name="Current water used", unique_id="current_water_sensor", device_class="water", unit_of_measurement="l")
        main_water_switch_settings = Settings(mqtt=self.mqtt_settings, entity=main_water_switch_info)
        watering_switch_settings = Settings(mqtt=self.mqtt_settings, entity=watering_switch_info)
        total_water_sensor_settings = Settings(mqtt=self.mqtt_settings, entity=total_water_sensor_info)
        current_water_sensor_settings = Settings(mqtt=self.mqtt_settings, entity=current_water_sensor_info)
        logger.info("Set up sensors.")

        self.main_water_switch = Switch(settings = main_water_switch_settings, command_callback = self.main_water_switch_callback)
        self.automatic_watering_switch = Switch(settings = watering_switch_settings, command_callback = self.automatic_watering_switch_callback)
        self.total_water_sensor = Sensor(settings = total_water_sensor_settings)
        self.current_water_sensor = Sensor(settings = current_water_sensor_settings)
        logger.info("Instantiated entities.")
        self.main_water_switch.off()
        self.automatic_watering_switch.off()

    def setupGpios(self):
        GPIO.setmode(GPIO.BCM)  # or GPIO.BOARD depending on your pin numbering preference
        GPIO.setup(GPIO_WATER_SENSOR, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        GPIO.add_event_detect(channel=GPIO_WATER_SENSOR, edge=GPIO.BOTH, callback=self.total_water_sensor_callback, bouncetime=10)
        GPIO.setup(GPIO_MAIN_WATER, GPIO.OUT, initial=GPIO.LOW)
        GPIO.setup(GPIO_AUTOMATIC_WATER, GPIO.OUT, initial=GPIO.LOW)

    def main_water_switch_callback(self, client: Client, user_data, message: MQTTMessage):
        logger.info(f"Received main_water_switch_callback message: {message}")
        payload = message.payload.decode('utf-8')
        logger.info(f"Received message payload: {payload}")
        if payload == 'ON':
            self.enableMainWater()
            self.main_time = 30 # * 60 # 15 minutes
            if self.automatic_time <= 0:
                self.disableAutomaticWatering()
        elif payload == 'OFF':
            self.disableMainWater()
            self.main_time = 0
            if self.automatic_time <= 0:
                self.disableAutomaticWatering()
            

    def automatic_watering_switch_callback(self, client: Client, user_data, message: MQTTMessage):
        logger.info(f"Received automatic_watering_switch_callback message: {message}")
        payload = message.payload.decode('utf-8')
        logger.info(f"Received message payload: {payload}")
        if payload == 'ON':
            self.enableAutomaticWatering()
            self.automatic_time = 15 # * 60 # 15 minutes
        elif payload == 'OFF':
            self.disableAutomaticWatering()
            self.automatic_time = 0
            if self.main_time <= 0:
                self.disableMainWater()

    def total_water_sensor_callback(self, channel):
        if not GPIO.input(channel):
            self.current_water_counter += 1


    def enableAutomaticWatering(self):
        logger.info("Switching automatic watering ON")
        if self.main_water_switch is not None:
            GPIO.output(GPIO_MAIN_WATER, GPIO.HIGH)
            self.main_water_switch.on()
        if self.automatic_watering_switch is not None:
            GPIO.output(GPIO_AUTOMATIC_WATER, GPIO.HIGH)
            self.automatic_watering_switch.on()

    def enableMainWater(self):
        logger.info("Switching main water ON")
        if self.main_water_switch is not None:
            GPIO.output(GPIO_MAIN_WATER, GPIO.HIGH)
            self.main_water_switch.on()
            self.current_water_usage = 0.0

    def disableAutomaticWatering(self):
        logger.info("Switching automatic watering OFF")
        GPIO.output(GPIO_AUTOMATIC_WATER, GPIO.LOW)
        if self.automatic_watering_switch is not None:
            self.automatic_watering_switch.off()

    def disableMainWater(self):
        GPIO.output(GPIO_MAIN_WATER, GPIO.LOW)
        GPIO.output(GPIO_AUTOMATIC_WATER, GPIO.LOW)
        logger.info("Switching main water OFF") 
        if self.main_water_switch is not None:
            self.main_water_switch.off()


    # Main loop to simulate sensor data and handle switch logic
    def main_loop(self):
        try:
            if self.main_time > 0:
                self.main_time -= 1
                if self.main_time <= 0:
                    logger.info("Main water time expired")
                    self.disableMainWater() 
                    if self.automatic_time <= 0:
                        self.disableAutomaticWatering()
            if self.automatic_time > 0:
                self.automatic_time -= 1
                if self.automatic_time <= 0:
                    logger.info("Automatic watering time expired")
                    self.disableAutomaticWatering()
                    if self.main_time <= 0:
                        self.disableMainWater()
            if self.current_water_counter > 0:
                usage = self.current_water_counter * 1.0
                self.current_water_counter = 0
                total_water_usage += usage
                self.database.save_accumulated_value(total_water_usage)
                self.current_water_usage += usage
                self.total_water_sensor.set_state(f"{total_water_usage:.1f}")
                self.current_water_sensor.set_state(f"{self.current_water_usage:.1f}")

            time.sleep(1)  # Pause before the next update
        except Exception as e:
            logger.error(f"Error in main loop: {e}")
            time.sleep(5)  # Delay to avoid rapid looping in case of persistent errors

    # Gracefully disconnect the MQTT client
    def disconnect_mqtt_client(client):
        try:
            client.disconnect()
            logger.info("Disconnected from MQTT broker")
        except Exception as e:
            logger.error(f"Failed to disconnect from MQTT broker: {e}")

    # Entry point for the script
if __name__ == "__main__":
    try:
        setupSafeGpios()
        watercontrol = WaterControl()
        watercontrol.setupGpios()
        watercontrol.setup_mqtt_client()
        watercontrol.setup_device()
        total_water_usage = watercontrol.database.load_accumulated_value()
        watercontrol.total_water_sensor.set_state(f"{total_water_usage:.1f}")
        watercontrol.current_water_sensor.set_state(f"{watercontrol.current_water_usage:.1f}")
        while True:
            watercontrol.main_loop()
    except KeyboardInterrupt:
        logger.info("Script interrupted by user")
    finally:
        logger.info("Script terminated")
        watercontrol.disableMainWater()
        watercontrol.disableAutomaticWatering()
        GPIO.cleanup()
