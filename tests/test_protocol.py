from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import unittest


MODULE_PATH = Path(__file__).parents[1] / "tools" / "modbus_usb_client.py"
SPEC = spec_from_file_location("modbus_usb_client", MODULE_PATH)
CLIENT = module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = CLIENT
SPEC.loader.exec_module(CLIENT)


class ProtocolTests(unittest.TestCase):
    def test_standard_crc_vector(self):
        payload = bytes.fromhex("01 03 00 00 00 0A")
        self.assertEqual(CLIENT.add_crc(payload), bytes.fromhex("01 03 00 00 00 0A C5 CD"))

    def test_write_high_coil_frame(self):
        frame = CLIENT.fixed_request(1, CLIENT.FUNCTION_WRITE_SINGLE_COIL, 1, 0xFF00)
        self.assertEqual(frame, bytes.fromhex("01 05 00 01 FF 00 DD FA"))

    def test_lsb_first_bit_unpacking(self):
        response = bytes.fromhex("01 02 02 8D 01 00 00")
        self.assertEqual(
            CLIENT.decode_bits(response, 10),
            [True, False, True, True, False, False, False, True, True, False],
        )

    def test_big_endian_register_unpacking(self):
        response = bytes.fromhex("01 04 04 12 34 AB CD 00 00")
        self.assertEqual(CLIENT.decode_registers(response), [0x1234, 0xABCD])


if __name__ == "__main__":
    unittest.main()
