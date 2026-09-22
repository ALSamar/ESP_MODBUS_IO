"""Host-side PWM wire-format, validation and GUI compatibility tests; no board needed."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
from types import ModuleType
import unittest
from unittest.mock import MagicMock, patch


TOOLS = Path(__file__).parents[1] / "tools"


def load_module(name, path):
    spec = spec_from_file_location(name, path)
    module = module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


CLIENT = load_module("modbus_usb_client", TOOLS / "modbus_usb_client.py")
serial_stub = ModuleType("serial")
serial_stub.tools = ModuleType("serial.tools")
serial_stub.tools.list_ports = ModuleType("serial.tools.list_ports")
with patch.dict(sys.modules, {"serial": serial_stub, "serial.tools": serial_stub.tools,
                             "serial.tools.list_ports": serial_stub.tools.list_ports,
                             "webview": ModuleType("webview")}):
    GUI = load_module("modbus_usb_gui", TOOLS / "modbus_usb_gui.py")


def register_reply(values, function=3):
    return CLIENT.add_crc(bytes((1, function, 2 * len(values))) +
                          b"".join(value.to_bytes(2, "big") for value in values))


class PwmClientTests(unittest.TestCase):
    def setUp(self):
        self.client = CLIENT.Client("COM_TEST")

    def test_pwm_writes_one_atomic_record_with_uint32_frequency(self):
        self.client.transact = MagicMock(return_value=CLIENT.add_crc(bytes.fromhex("01 10 03 08 00 04")))
        self.client.set_pwm(2, 100000, "33.33")
        expected = CLIENT.add_crc(bytes.fromhex("01 10 03 08 00 04 08 00 01 86 A0 0D 05 00 01"))
        self.client.transact.assert_called_once_with(expected)

    def test_duty_endpoints_and_fraction(self):
        for source, expected in [("0", 0), ("100", 10000), ("0.01", 1), ("12.34", 1234)]:
            with self.subTest(source=source):
                self.assertEqual(CLIENT.duty_to_basis_points(source), expected)

    def test_invalid_duty_never_writes(self):
        self.client.transact = MagicMock()
        for duty in ("NaN", "sNaN", "Infinity", "-Infinity", "-0.01", "100.01", "0.001", "1e999999", "1e-999999", "99.999999999999999999999999999999", "word"):
            with self.subTest(duty=duty), self.assertRaises(ValueError):
                self.client.set_pwm(0, 1000, duty)
        self.client.transact.assert_not_called()

    def test_invalid_frequency_channel_and_enable_never_write(self):
        self.client.transact = MagicMock()
        for frequency in (9, 100001, 1.5, True, float("nan")):
            with self.subTest(frequency=frequency), self.assertRaises(ValueError):
                self.client.set_pwm(0, frequency, 50)
        for channel in (-1, 34, 1.5, True):
            with self.subTest(channel=channel), self.assertRaises(ValueError):
                self.client.set_pwm(channel, 1000, 50)
        with self.assertRaises(ValueError):
            self.client.set_pwm(0, 1000, 50, "false")
        self.client.transact.assert_not_called()

    def test_readback_uses_high_word_first(self):
        self.client.transact = MagicMock(return_value=register_reply([1, 0x86A0, 1234, 1]))
        config = self.client.read_pwm(33)
        self.assertEqual(config, CLIENT.PwmConfig(100000, 1234, True))
        self.assertEqual(config.duty_percent, "12.34")
        self.client.transact.assert_called_once_with(CLIENT.fixed_request(1, 3, 0x0384, 4))

    def test_stop_preserves_frequency_and_duty(self):
        self.client.transact = MagicMock(side_effect=[register_reply([1, 4464, 2501, 1]),
            CLIENT.add_crc(bytes.fromhex("01 10 03 04 00 04"))])
        self.client.stop_pwm(1)
        expected = CLIENT.add_crc(bytes.fromhex("01 10 03 04 00 04 08 00 01 11 70 09 C5 00 00"))
        self.assertEqual(self.client.transact.call_args_list[1].args[0], expected)

    def test_disabled_configuration_writes_zero_enable(self):
        self.client.transact = MagicMock(return_value=CLIENT.add_crc(bytes.fromhex("01 10 03 00 00 04")))
        self.client.set_pwm(0, 10, 100, False)
        self.assertEqual(self.client.transact.call_args.args[0][-4:-2], b"\x00\x00")

    def test_mismatched_write_echo_is_rejected(self):
        self.client.transact = MagicMock(return_value=CLIENT.add_crc(bytes.fromhex("01 10 03 04 00 04")))
        with self.assertRaises(CLIENT.ModbusError):
            self.client.set_pwm(0, 1000, 50)

    def test_mismatched_read_function_count_or_length_is_rejected(self):
        for response in (register_reply([0, 1000, 5000, 1], 4), register_reply([0, 1000, 5000]),
                         register_reply([0, 1000, 5000, 1])[:-1]):
            self.client.transact = MagicMock(return_value=response)
            with self.subTest(response=response), self.assertRaises(CLIENT.ModbusError):
                self.client.read_pwm(0)

    def test_invalid_device_record_is_rejected(self):
        for values in ([0, 0, 5000, 0], [2, 0, 5000, 1], [0, 1000, 10001, 1], [0, 1000, 5000, 2]):
            self.client.transact = MagicMock(return_value=register_reply(values))
            with self.subTest(values=values), self.assertRaises(CLIENT.ModbusError):
                self.client.read_pwm(0)

    def test_cli_accepts_new_commands_and_pwm_mode(self):
        parser = CLIENT.build_parser()
        args = parser.parse_args(["--port", "COM_TEST", "pwm-set", "33", "100000", "50.01", "--disabled"])
        self.assertEqual((args.channel, args.frequency, args.duty, args.disabled), (33, 100000, "50.01", True))
        self.assertEqual(parser.parse_args(["--port", "COM_TEST", "set-mode", "2", "5"]).mode, 5)
        for command in ("pwm-read", "pwm-stop"):
            self.assertEqual(parser.parse_args(["--port", "COM_TEST", command, "2"]).channel, 2)

    def transact_response(self, response):
        connection = MagicMock()
        connection.in_waiting = len(response)
        connection.read.return_value = response
        serial = MagicMock()
        serial.Serial.return_value.__enter__.return_value = connection
        with patch.dict(sys.modules, {"serial": serial}):
            return self.client.transact(CLIENT.fixed_request(1, 3, 0x0300, 4))

    def test_serial_crc_wrong_slave_and_wrong_function_rejected(self):
        good = register_reply([0, 1000, 5000, 1])
        for response in (good[:-1] + bytes((good[-1] ^ 1,)), CLIENT.add_crc(bytes((2,)) + good[1:-2]),
                         register_reply([0, 1000, 5000, 1], 4)):
            with self.subTest(response=response), self.assertRaises(CLIENT.ModbusError):
                self.transact_response(response)

    def test_serial_resource_busy_explained(self):
        with self.assertRaisesRegex(CLIENT.ModbusError, "PWM channel/timer"):
            self.transact_response(CLIENT.add_crc(bytes.fromhex("01 83 06")))

    def test_serial_valid_read_accepted(self):
        response = register_reply([0, 1000, 5000, 1])
        self.assertEqual(self.transact_response(response), response)


class PwmGuiTests(unittest.TestCase):
    def setUp(self):
        self.api = GUI.Api()
        self.client = MagicMock()
        self.api._client = MagicMock(return_value=self.client)

    def info(self, flags=4):
        self.client.read_registers.side_effect = lambda function, start, count: (
            [0x0101, 0x0101, 34, 18, flags] if start == 0x0200 else [8, 4])

    def test_old_firmware_does_not_read_new_registers(self):
        self.info(flags=0)
        result = self.api.get_info("COM_TEST", 1)
        self.assertTrue(result["ok"])
        self.assertFalse(result["data"]["pwm"])
        self.assertEqual(result["data"]["usable_digital"], 31)
        self.client.read_registers.assert_called_once_with(3, 0x0200, 5)

    def test_new_firmware_enables_optional_channels(self):
        self.info(flags=6)
        result = self.api.get_info("COM_TEST", 1)
        self.assertEqual(result["data"]["usable_digital"], 34)
        self.assertEqual(result["data"]["pwm_channels"], 8)
        self.assertEqual(result["data"]["pwm_frequencies"], 4)

    def test_unsupported_pwm_never_writes(self):
        self.info(flags=0)
        self.assertFalse(self.api.configure_pwm("COM_TEST", 1, 1, "1000", "50", True)["ok"])
        self.assertFalse(self.api.stop_pwm("COM_TEST", 1, 1)["ok"])
        self.assertFalse(self.api.read_pwm("COM_TEST", 1)["ok"])
        self.client.set_pwm.assert_not_called()
        self.client.stop_pwm.assert_not_called()
        self.client.read_pwm.assert_not_called()

    def test_reserved_channel_validation_changes_with_capability(self):
        self.info(flags=4)
        self.assertFalse(self.api.configure_pwm("COM_TEST", 1, 33, "1000", "50", True)["ok"])
        self.client.set_pwm.assert_not_called()
        self.info(flags=6)
        self.client.read_pwm.return_value = CLIENT.PwmConfig(1000, 5000, True)
        result = self.api.configure_pwm("COM_TEST", 1, 33, "1000", "50", True)
        self.assertTrue(result["ok"])
        self.client.set_pwm.assert_called_once_with(33, 1000, "50", True)

    def test_fractional_frequency_not_truncated(self):
        self.info()
        self.assertFalse(self.api.configure_pwm("COM_TEST", 1, 1, "10.5", "50", True)["ok"])
        self.client.set_pwm.assert_not_called()

    def test_digital_pwm_is_not_reported_as_latched_high_or_low(self):
        modes = [0] * 34
        modes[1], modes[2], modes[3] = 5, 4, 3
        def registers(function, start, count):
            if start == 0x0200:
                return [0x0101, 0x0101, 34, 18, 6]
            if start == 0x0205:
                return [8, 4]
            return modes[:count] if start == 0 else list(range(count))
        self.client.read_registers.side_effect = registers
        self.client.read_bits.side_effect = lambda function, start, count: [True] * count
        result = self.api.read_digital("COM_TEST", 1)
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["data"]), 34)
        self.assertIsNone(result["data"][1]["output"])
        self.assertIsNone(result["data"][1]["input"])
        self.assertEqual(result["data"][1]["mode_name"], "PWM 输出")
        self.assertTrue(result["data"][3]["output"])
        for call in self.client.read_bits.call_args_list:
            function, start, count = call.args
            if function == 2:
                self.assertFalse(set(range(start, start + count)) & {1, 2})

    def test_adc_refresh_preserves_pwm_and_digital_outputs(self):
        def registers(function, start, count):
            if function == 3:
                if start == 0x0200:
                    return [0x0101, 0x0101, 34, 18, 4]
                if start == 0x0205:
                    return [8, 4]
                return [5, 3] + [0] * 16
            self.assertGreaterEqual(start & 0xFF, 2)
            return [123] * count
        self.client.read_registers.side_effect = registers
        result = self.api.read_analog("COM_TEST", 1)
        self.assertTrue(result["ok"])
        self.assertIsNone(result["data"][0]["raw"])
        self.assertIsNone(result["data"][1]["raw"])
        self.assertEqual(result["data"][2]["raw"], 123)


if __name__ == "__main__":
    unittest.main()
