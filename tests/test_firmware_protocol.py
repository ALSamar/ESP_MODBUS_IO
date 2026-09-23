"""Execute production Modbus C code against mock IO with a host compiler.

Set PWM_TEST_CC or CC to a host C compiler. No board or Python packages required.
"""
import ctypes
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from test_pwm_native import host_compiler

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "tests" / "native_protocol"


def crc(data):
    value = 0xffff
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = (value >> 1) ^ 0xa001 if value & 1 else value >> 1
    return value


def frame(data):
    data = bytes(data)
    return data + crc(data).to_bytes(2, "little")


def fixed(function, address, value, slave=1):
    return frame(bytes((slave, function)) + address.to_bytes(2, "big") + value.to_bytes(2, "big"))


def multi(address, values, slave=1):
    payload = b"".join(v.to_bytes(2, "big") for v in values)
    return frame(bytes((slave, 16)) + address.to_bytes(2, "big") +
                 len(values).to_bytes(2, "big") + bytes((len(payload),)) + payload)


def pwm(channel, frequency, duty, enabled, slave=1):
    return multi(0x300 + 4 * channel, [frequency >> 16, frequency & 65535, duty, enabled], slave)


class FirmwareProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = host_compiler()
        if compiler is None:
            raise unittest.SkipTest("C protocol tests require a host C compiler (PWM_TEST_CC/CC)")
        cls.tempdir = tempfile.TemporaryDirectory(prefix="esp_io_protocol_")
        cls.addClassCleanup(cls.tempdir.cleanup)
        binary = Path(cls.tempdir.name) / ("protocol.dll" if os.name == "nt" else "protocol.so")
        command = [*compiler, "-std=c99", "-shared", "-fno-builtin", "-Wall", "-Wextra", "-Werror",
                   "-I", str(NATIVE), "-I", str(ROOT / "main"),
                   str(ROOT / "main" / "modbus_server.c"), str(NATIVE / "mocks.c"),
                   "-o", str(binary)]
        if os.name != "nt":
            command.insert(len(compiler), "-fPIC")
        subprocess.run(command, check=True)
        cls.lib = ctypes.CDLL(str(binary))
        for name in ("request_buffer", "response_buffer"):
            getattr(cls.lib, name).restype = ctypes.POINTER(ctypes.c_uint8)
        cls.lib.response_size.restype = ctypes.c_uint
        if sys.platform == "win32":
            import _ctypes
            cls.addClassCleanup(_ctypes.FreeLibrary, cls.lib._handle)

    def setUp(self):
        self.lib.reset_mock()

    def send(self, request):
        ctypes.memmove(self.lib.request_buffer(), request, len(request))
        error = self.lib.process_request(len(request))
        response = bytes(self.lib.response_buffer()[:self.lib.response_size()])
        if response:
            self.assertEqual(crc(response[:-2]), int.from_bytes(response[-2:], "little"))
        return error, response

    def registers(self, address, quantity):
        error, response = self.send(fixed(3, address, quantity))
        self.assertEqual(error, 0)
        self.assertEqual(response[1:3], bytes((3, quantity * 2)))
        return [int.from_bytes(response[i:i+2], "big") for i in range(3, len(response)-2, 2)]

    def exception(self, request, code):
        error, response = self.send(request)
        self.assertEqual(error, 0)
        self.assertEqual(response[:3], bytes((request[0], request[1] | 128, code)))

    def test_legacy_info_and_pwm_capabilities(self):
        self.assertEqual(self.registers(0x200, 7), [0x102, 0x102, 34, 18, 61, 8, 4])

    def test_debugger_variable_frame_and_exception(self):
        request = frame(bytes((1, 0x41, 10, 0)))
        error, response = self.send(request)
        self.assertEqual(error, 0)
        self.assertEqual(response[:4], bytes((1, 0x41, 10, 25)))
        self.assertEqual(response[4:-2], bytes(25))
        self.exception(frame(bytes((1, 0x41, 99, 0))), 1)

    def test_defaults_and_uint32_frequency_round_trip(self):
        self.assertEqual(self.registers(0x304, 4), [0, 1000, 5000, 0])
        request = pwm(1, 100000, 1234, 1)
        error, response = self.send(request)
        self.assertEqual(error, 0)
        self.assertEqual(response[:6], request[:6])
        self.assertEqual(self.registers(0x304, 4), [1, 34464, 1234, 1])
        self.assertEqual(self.registers(1, 1), [5])

    def test_duty_and_frequency_endpoints(self):
        for frequency, duty in ((10, 0), (100000, 10000)):
            self.assertEqual(self.send(pwm(1, frequency, duty, 1))[1][1], 16)

    def test_invalid_values_never_change_io(self):
        for frequency, duty, enabled in ((9,5000,1), (100001,5000,1),
                                         (1000,10001,1), (1000,0,2), (0,0,0)):
            with self.subTest(frequency=frequency, duty=duty, enabled=enabled):
                self.exception(pwm(1, frequency, duty, enabled), 3)
        self.assertEqual(self.lib.write_count(), 0)

    def test_partial_and_cross_record_writes_rejected(self):
        for request in (fixed(6,0x306,5000), multi(0x304,[0,1000]),
                        multi(0x305,[0,1000,5000,1]),
                        multi(0x304,[0,1000,5000,1,0,1000,5000,1])):
            self.exception(request, 3)
        self.assertEqual(self.lib.write_count(), 0)

    def test_reserved_and_out_of_range_addresses(self):
        for request in (pwm(31,1000,5000,1), pwm(34,1000,5000,1),
                        fixed(3,0x37c,4), fixed(3,0xffff,2)):
            self.exception(request, 2)

    def test_resource_exhaustion_is_busy_and_preserves_state(self):
        self.lib.force_error(0x105)
        for request in (pwm(1,1000,5000,1), fixed(6,1,5), multi(1,[5])):
            self.exception(request, 6)
        self.assertEqual(self.lib.write_count(), 0)

    def test_disable_and_legacy_mode_interoperation(self):
        self.send(pwm(1,1000,2500,1))
        self.send(pwm(1,2000,5000,0))
        self.assertEqual(self.registers(1,1), [0])
        self.assertEqual(self.registers(0x304,4), [0,2000,5000,0])
        self.send(fixed(6,1,5))
        self.exception(fixed(4,0,1), 4)
        self.send(fixed(5,1,0xff00))
        self.assertEqual(self.registers(1,1), [3])
        self.assertEqual(self.registers(0x304,4)[3], 0)

    def test_batch_mode_pwm_is_rejected_before_changes(self):
        self.exception(multi(0,[0,5]), 3)
        self.assertEqual(self.lib.write_count(), 0)
        self.assertEqual(self.send(multi(1,[5]))[1][1], 16)

    def test_broadcast_writes_and_ignored_reads(self):
        self.assertEqual(self.send(pwm(1,50,750,1,0))[1], b"")
        self.assertEqual(self.lib.write_count(), 1)
        self.assertEqual(self.send(fixed(3,0x304,4,0))[1], b"")
        self.assertEqual(self.registers(0x304,4), [0,50,750,1])
        self.assertEqual(self.send(pwm(1,0,750,1,0))[1], b"")
        self.assertEqual(self.lib.write_count(), 1)

    def test_crc_other_slave_and_truncated_valid_crc_requests(self):
        self.assertEqual(self.send(pwm(1,50,750,1,2))[1], b"")
        bad = bytearray(pwm(1,50,750,1))
        bad[8] ^= 1
        self.assertEqual(self.send(bytes(bad))[0], 0x109)
        self.assertEqual(self.send(frame([1,16]))[0], 0x104)
        self.assertEqual(self.send(frame([1,3]))[0], 0x104)
        self.assertEqual(self.lib.write_count(), 0)

    def test_legacy_gpio_and_adc(self):
        self.assertEqual(self.send(fixed(5,1,0xff00))[1][1], 5)
        self.assertEqual(self.send(fixed(1,1,1))[1][3], 1)
        self.assertEqual(self.send(fixed(6,1,0))[1][1], 6)
        self.assertEqual(self.send(fixed(4,0,1))[1][1], 4)


if __name__ == "__main__":
    unittest.main()
