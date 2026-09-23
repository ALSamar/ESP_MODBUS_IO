"""Host-side debugger framing, validation and GUI sampling tests."""
from pathlib import Path
from importlib.util import module_from_spec, spec_from_file_location
import sys
from types import ModuleType
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch


TOOLS = Path(__file__).parents[1] / "tools"


def load(name, filename):
    spec = spec_from_file_location(name, TOOLS / filename)
    module = module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


CLIENT = load("modbus_usb_client", "modbus_usb_client.py")
serial_stub = ModuleType("serial")
serial_stub.tools = ModuleType("serial.tools")
serial_stub.tools.list_ports = ModuleType("serial.tools.list_ports")
with patch.dict(sys.modules, {"serial": serial_stub, "serial.tools": serial_stub.tools,
                              "serial.tools.list_ports": serial_stub.tools.list_ports,
                              "webview": ModuleType("webview")}):
    GUI = load("modbus_usb_gui", "modbus_usb_gui.py")


class FakeSerial:
    def __init__(self, response):
        self.response = bytearray(response)
        self.written = b""

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def reset_input_buffer(self):
        pass

    def write(self, request):
        self.written = request

    def flush(self):
        pass

    @property
    def in_waiting(self):
        return len(self.response)

    def read(self, count):
        chunk = self.response[:count]
        del self.response[:count]
        return chunk


class DebuggerClientTests(unittest.TestCase):
    def setUp(self):
        self.client = CLIENT.Client("COM_TEST")

    def test_variable_response_framing(self):
        response = CLIENT.add_crc(bytes((1, 0x41, CLIENT.DBG_I2C_SCAN, 16)) + bytes(range(16)))
        fake = FakeSerial(response)
        stub = ModuleType("serial")
        stub.Serial = lambda *args, **kwargs: fake
        with patch.dict(sys.modules, {"serial": stub}):
            data = self.client.debugger(CLIENT.DBG_I2C_SCAN)
        self.assertEqual(data, bytes(range(16)))
        self.assertEqual(fake.written, CLIENT.add_crc(bytes((1, 0x41, 3, 0))))

    def test_spi_config_and_transfer_payloads(self):
        self.client.transact = MagicMock(side_effect=[
            CLIENT.add_crc(bytes((1, 0x41, 5, 0))),
            CLIENT.add_crc(bytes((1, 0x41, 7, 3, 0x11, 0x22, 0x33))),
        ])
        self.client.spi_config(6, 7, None, None, 3, 1_000_000)
        self.assertEqual(self.client.spi_transfer(bytes.fromhex("9f 00 00")), bytes.fromhex("11 22 33"))
        self.assertEqual(self.client.transact.call_args_list[0].args[0][:-2],
                         bytes.fromhex("01 41 05 09 06 07 FF FF 03 00 0F 42 40"))
        self.assertEqual(self.client.transact.call_args_list[1].args[0][:-2],
                         bytes.fromhex("01 41 07 04 03 9F 00 00"))

    def test_invalid_bus_parameters_do_not_send(self):
        self.client.transact = MagicMock()
        for action in (
            lambda: self.client.i2c_config(4, 4),
            lambda: self.client.i2c_transfer(0x78, b"x"),
            lambda: self.client.i2c_transfer(0x50, bytes(129)),
            lambda: self.client.spi_config(1, 1, 2, 3),
            lambda: self.client.spi_transfer(b""),
            lambda: self.client.uart_config(25, 25),
        ):
            with self.subTest(action=action), self.assertRaises(ValueError):
                action()
        self.client.transact.assert_not_called()

    def test_debugger_status_decode(self):
        payload = bytes((7, 4, 5, 6, 7, 8, 9, 25, 26)) + \
                  (100000).to_bytes(4, "big") + (1000000).to_bytes(4, "big") + \
                  (115200).to_bytes(4, "big") + bytes((3, 8, 0, 1))
        self.client.debugger = MagicMock(return_value=payload)
        status = self.client.debugger_status()
        self.assertTrue(status["i2c"] and status["spi"] and status["uart"])
        self.assertEqual(status["uart_pins"], [25, 26])
        self.assertEqual(status["spi_hz"], 1000000)

    def test_gui_waveform_rejects_busy_pin(self):
        api = GUI.Api()
        client = MagicMock()
        client.read_registers.return_value = [6]
        client.__enter__.return_value = client
        api._client = lambda *args: client
        result = api.sample_waveform("COM_TEST", 1, ["D4"])
        self.assertFalse(result["ok"])
        client.read_bits.assert_not_called()

    def test_gui_terminal_text_hex_and_line_endings(self):
        class Port:
            def __init__(self):
                self.sent = []
                self.received = bytearray(b"\x00\x7f")
                self.closed = False

            def write(self, data):
                self.sent.append(data)
                return len(data)

            @property
            def in_waiting(self):
                return len(self.received)

            def read(self, count):
                data = self.received[:count]
                del self.received[:count]
                return data

            def close(self):
                self.closed = True

        port = Port()
        api = GUI.Api()
        with patch.object(GUI.serial, "Serial", return_value=port, create=True):
            self.assertTrue(api.terminal_open("COM_TEST", 115200, 8, "N", 1)["ok"])
        self.assertEqual(api.terminal_read()["data"]["hex"], "00 7F")
        self.assertTrue(api.terminal_send("你好", False, "CRLF")["ok"])
        self.assertTrue(api.terminal_send("aa 55", True, "")["ok"])
        self.assertFalse(api.terminal_send("zz", True, "")["ok"])
        self.assertEqual(port.sent, ["你好".encode() + b"\r\n", b"\xaa\x55"])
        api.terminal_close()
        self.assertTrue(port.closed)

    def test_gui_autoselects_control_cdc_interface(self):
        ports = [
            SimpleNamespace(device="COM3", description="PC", vid=None, pid=None, location=None),
            SimpleNamespace(device="COM41", description="bridge", vid=0x303A, pid=0x4002,
                            location="1-9.1:x.2"),
            SimpleNamespace(device="COM43", description="control", vid=0x303A, pid=0x4002,
                            location="1-9.1:x.0"),
        ]
        with patch.object(GUI.serial.tools.list_ports, "comports", return_value=ports, create=True):
            self.assertEqual(GUI.Api().get_ports()["recommended"], "COM43")


if __name__ == "__main__":
    unittest.main()
