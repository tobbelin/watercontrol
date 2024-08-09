import unittest
from unittest.mock import patch, MagicMock, call
import sqlite3
import RPi.GPIO as GPIO
import os

# Assuming the above code is in a module named 'watercontrol'
from watercontrol import Database, WaterControl, setupSafeGpios

class TestDatabase(unittest.TestCase):

    @patch('watercontrol.sqlite3.connect')
    def test_initialize_db(self, mock_connect):
        mock_conn = mock_connect.return_value
        mock_cursor = mock_conn.cursor.return_value

        db = Database()

        mock_connect.assert_called_once_with('watercontrol.db')
        mock_cursor.execute.assert_has_calls([
            call('''
            CREATE TABLE IF NOT EXISTS watercontrol (
                id INTEGER PRIMARY KEY,
                name TEXT,
                value FLOAT
            )
            '''),
            call('''
            INSERT INTO watercontrol (id, name, value) 
            VALUES (1, 'total_water', 0)
            ON CONFLICT(id) DO NOTHING
            ''')
        ])
        mock_conn.commit.assert_called_once()

    @patch('watercontrol.sqlite3.connect')
    def test_load_accumulated_value(self, mock_connect):
        mock_conn = mock_connect.return_value
        mock_cursor = mock_conn.cursor.return_value
        mock_cursor.fetchone.return_value = (10.0,)

        db = Database()
        result = db.load_accumulated_value()

        mock_cursor.execute.assert_called_once_with('SELECT value FROM watercontrol WHERE id = 1')
        self.assertEqual(result, 10.0)

    @patch('watercontrol.sqlite3.connect')
    def test_save_accumulated_value(self, mock_connect):
        mock_conn = mock_connect.return_value
        mock_cursor = mock_conn.cursor.return_value

        db = Database()
        db.save_accumulated_value(15.5)

        mock_cursor.execute.assert_called_once_with('''
        UPDATE watercontrol
        SET value = ?
        WHERE id = 1
        ''', (15.5,))
        mock_conn.commit.assert_called_once()


class TestWaterControl(unittest.TestCase):

    @patch('watercontrol.GPIO.setup')
    @patch('watercontrol.GPIO.setmode')
    def test_setup_gpios(self, mock_setmode, mock_setup):
        water_control = WaterControl()
        water_control.setupGpios()

        mock_setmode.assert_called_once_with(GPIO.BCM)
        mock_setup.assert_has_calls([
            call(17, GPIO.IN, pull_up_down=GPIO.PUD_UP),
            call(27, GPIO.OUT, initial=GPIO.LOW),
            call(22, GPIO.OUT, initial=GPIO.LOW)
        ])

    @patch('watercontrol.GPIO.setup')
    @patch('watercontrol.GPIO.setmode')
    def test_setup_safe_gpios(self, mock_setmode, mock_setup):
        setupSafeGpios()

        mock_setmode.assert_called_once_with(GPIO.BCM)
        mock_setup.assert_has_calls([
            call(27, GPIO.OUT, initial=GPIO.LOW),
            call(22, GPIO.OUT, initial=GPIO.LOW)
        ])

    @patch('watercontrol.Client')
    @patch('watercontrol.WaterControl.setup_device')
    @patch('watercontrol.Database')
    def test_setup_mqtt_client(self, mock_database, mock_setup_device, mock_mqtt_client):
        water_control = WaterControl()
        water_control.setup_mqtt_client()

        mock_mqtt_client.assert_called_once()
        self.assertIsNotNone(water_control.mqtt_settings)

    @patch('watercontrol.GPIO.output')
    @patch('watercontrol.Switch.on')
    def test_enable_main_water(self, mock_switch_on, mock_gpio_output):
        water_control = WaterControl()
        water_control.main_water_switch = MagicMock()
        
        water_control.enableMainWater()
        
        mock_gpio_output.assert_called_once_with(27, GPIO.HIGH)
        mock_switch_on.assert_called_once()

    @patch('watercontrol.GPIO.output')
    @patch('watercontrol.Switch.off')
    def test_disable_main_water(self, mock_switch_off, mock_gpio_output):
        water_control = WaterControl()
        water_control.main_water_switch = MagicMock()
        
        water_control.disableMainWater()
        
        mock_gpio_output.assert_has_calls([
            call(27, GPIO.LOW),
            call(22, GPIO.LOW)
        ])
        mock_switch_off.assert_called_once()

    @patch('watercontrol.time.sleep', return_value=None)
    @patch('watercontrol.WaterControl.disableMainWater')
    @patch('watercontrol.WaterControl.disableAutomaticWatering')
    @patch('watercontrol.WaterControl.setup_device')
    @patch('watercontrol.Database.load_accumulated_value', return_value=0)
    def test_main_loop(self, mock_load_accumulated_value, mock_setup_device, mock_disable_auto_watering, mock_disable_main_water, mock_sleep):
        water_control = WaterControl()
        water_control.total_water_sensor = MagicMock()
        water_control.current_water_sensor = MagicMock()
        
        water_control.main_time = 1
        water_control.automatic_time = 1
        water_control.current_water_counter = 1

        with patch('builtins.print'):
            with self.assertRaises(StopIteration):
                water_control.main_loop()
        
        mock_disable_main_water.assert_called()
        mock_disable_auto_watering.assert_called()
        mock_load_accumulated_value.assert_called_once()
        water_control.total_water_sensor.set_state.assert_called()
        water_control.current_water_sensor.set_state.assert_called()

    @patch('watercontrol.GPIO.input', return_value=0)
    def test_total_water_sensor_callback(self, mock_gpio_input):
        water_control = WaterControl()
        water_control.current_water_counter = 0

        water_control.total_water_sensor_callback(17)
        
        self.assertEqual(water_control.current_water_counter, 1)


if __name__ == '__main__':
    unittest.main()
