import unittest
from unittest.mock import patch, MagicMock, call
import logging
from watercontrol import setup_logging, setup_mqtt_client, setup_device, on_message, main_loop, disconnect_mqtt_client

class TestMQTTDeviceScript(unittest.TestCase):

    @patch('mqtt_device_script.SysLogHandler')
    def test_setup_logging(self, MockSysLogHandler):
        logger = setup_logging()
        self.assertIsInstance(logger, logging.Logger)
        MockSysLogHandler.assert_called_once_with(address='/dev/log')
        self.assertEqual(logger.level, logging.INFO)
        self.assertTrue(logger.hasHandlers())

    @patch('paho.mqtt.client.Client.connect')
    @patch('paho.mqtt.client.Client')
    def test_setup_mqtt_client(self, MockClient, MockConnect):
        mock_client = MockClient.return_value
        client = setup_mqtt_client()
        MockClient.assert_called_once()
        mock_client.username_pw_set.assert_called_once()
        MockConnect.assert_called_once_with('localhost', 1883, 60)
        self.assertEqual(client, mock_client)

    @patch('mqtt_device_script.Device')
    @patch('mqtt_device_script.Sensor')
    @patch('mqtt_device_script.Switch')
    @patch('mqtt_device_script.TimeControl')
    @patch('mqtt_device_script.NumberControl')
    def test_setup_device(self, MockNumberControl, MockTimeControl, MockSwitch, MockSensor, MockDevice):
        mock_client = MagicMock()
        mock_device = MockDevice.return_value
        mock_sensor = MockSensor.return_value
        mock_switch = MockSwitch.return_value

        sensor, switch = setup_device(mock_client)

        MockDevice.assert_called_once()
        MockSensor.assert_called_once_with(mock_device, id="temperature_sensor", name="Temperature", device_class="temperature", unit_of_measurement="°C")
        MockSwitch.assert_called_once_with(mock_device, id="control_switch", name="Control Switch")
        MockTimeControl.assert_called_once_with(mock_switch, name="Switch Time Control", min_value=0, max_value=3600, step=1, unit_of_measurement="s")
        MockNumberControl.assert_called_once_with(mock_switch, name="Switch Number Control", min_value=0, max_value=100, step=1, unit_of_measurement="V")
        mock_device.publish_config.assert_called_once()
        
        self.assertEqual(sensor, mock_sensor)
        self.assertEqual(switch, mock_switch)

    def test_on_message(self):
        mock_switch = MagicMock()
        mock_switch.state_topic = 'homeassistant/switch/my_device_control_switch/state'
        mock_msg = MagicMock(topic=mock_switch.state_topic, payload=b'ON')
        
        with patch('mqtt_device_script.logger') as mock_logger:
            on_message(None, None, mock_msg)
            mock_logger.info.assert_called_once_with("Switch state changed to: ON")

    @patch('time.sleep', return_value=None)  # Skip actual sleeping for the test
    def test_main_loop(self, mock_sleep):
        mock_sensor = MagicMock()
        mock_switch = MagicMock()
        mock_client = MagicMock()

        mock_switch.state = 'OFF'
        with patch('mqtt_device_script.client', mock_client):
            with patch('mqtt_device_script.logger') as mock_logger:
                with self.assertRaises(StopIteration):  # We will raise this to stop the loop after first iteration
                    def side_effect(*args, **kwargs):
                        raise StopIteration()
                    mock_sleep.side_effect = side_effect

                    main_loop(mock_sensor, mock_switch)

                mock_sensor.set_state.assert_called_once_with(25.0)
                mock_client.publish.assert_has_calls([
                    call(mock_sensor.state_topic, 25.0),
                ])
                mock_logger.info.assert_any_call("Sensor value published: 25.0 °C")

    @patch('paho.mqtt.client.Client.disconnect')
    def test_disconnect_mqtt_client(self, MockDisconnect):
        mock_client = MagicMock()
        disconnect_mqtt_client(mock_client)
        MockDisconnect.assert_called_once()


# If this script is run directly, run the unit tests
if __name__ == "__main__":
    unittest.main()
