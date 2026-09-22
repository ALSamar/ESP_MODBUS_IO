#!/usr/bin/env python3
"""Small Modbus RTU client for the ESP32-S3 USB CDC IO firmware."""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


FUNCTION_READ_COILS = 0x01
FUNCTION_READ_DISCRETE_INPUTS = 0x02
FUNCTION_READ_HOLDING_REGISTERS = 0x03
FUNCTION_READ_INPUT_REGISTERS = 0x04
FUNCTION_WRITE_SINGLE_COIL = 0x05
FUNCTION_WRITE_SINGLE_REGISTER = 0x06
FUNCTION_WRITE_MULTIPLE_REGISTERS = 0x10
PWM_BASE = 0x0300
PWM_RECORD_SIZE = 4
PWM_MIN_FREQUENCY = 10
PWM_MAX_FREQUENCY = 100_000
PWM_MODE = 5


class ModbusError(RuntimeError):
    pass


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for value in data:
        crc ^= value
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def add_crc(payload: bytes) -> bytes:
    checksum = crc16(payload)
    return payload + bytes((checksum & 0xFF, checksum >> 8))


def fixed_request(slave: int, function: int, address: int, value: int) -> bytes:
    if not 0 <= slave <= 247:
        raise ValueError("slave must be in the range 0..247")
    if not 0 <= address <= 0xFFFF or not 0 <= value <= 0xFFFF:
        raise ValueError("address and value must fit in 16 bits")
    return add_crc(bytes((slave, function, address >> 8, address & 0xFF,
                          value >> 8, value & 0xFF)))


def decode_bits(response: bytes, quantity: int) -> list[bool]:
    if len(response) < 5 or response[2] != (quantity + 7) // 8 or len(response) != response[2] + 5:
        raise ModbusError("bit response length does not match the requested count")
    byte_count = response[2]
    packed = response[3:3 + byte_count]
    return [bool((packed[index // 8] >> (index % 8)) & 1)
            for index in range(quantity)]


def decode_registers(response: bytes) -> list[int]:
    if len(response) < 5 or len(response) != response[2] + 5:
        raise ModbusError("register response length does not match its byte count")
    byte_count = response[2]
    payload = response[3:3 + byte_count]
    if byte_count % 2:
        raise ModbusError("register response has an odd byte count")
    return [int.from_bytes(payload[index:index + 2], "big")
            for index in range(0, len(payload), 2)]


def duty_to_basis_points(value: str | float | Decimal) -> int:
    """Convert percent exactly; silently rounding user-entered duty is unsafe."""
    try:
        duty = Decimal(str(value))
        if not duty.is_finite() or duty < 0 or duty > 100:
            raise ValueError("duty must be a finite percentage in the range 0..100")
        # Check decimal places before arithmetic so the Decimal context cannot
        # round a long input such as 99.999999999999999999999999999999 to 100.
        digits = duty.as_tuple().digits
        excess_places = max(0, -duty.as_tuple().exponent - 2)
        if excess_places and any(digits[max(0, len(digits) - excess_places):]):
            raise ValueError("duty supports at most two decimal places (0.01%)")
        return int(duty * 100)
    except InvalidOperation as error:
        raise ValueError("duty must be a percentage in the range 0..100") from error


def pwm_address(channel: int) -> int:
    if isinstance(channel, bool) or not isinstance(channel, int) or not 0 <= channel < 34:
        raise ValueError("digital channel must be in the range 0..33")
    return PWM_BASE + PWM_RECORD_SIZE * channel


@dataclass(frozen=True)
class PwmConfig:
    frequency: int
    duty_bp: int
    enabled: bool

    @property
    def duty_percent(self) -> str:
        return f"{self.duty_bp // 100}.{self.duty_bp % 100:02d}"


@dataclass
class Client:
    port: str
    slave: int = 1
    timeout: float = 0.5

    def transact(self, request: bytes) -> bytes:
        try:
            import serial
        except ImportError as error:
            raise ModbusError(
                "pyserial is missing; run: python -m pip install -r requirements.txt"
            ) from error

        with serial.Serial(self.port, baudrate=115200, timeout=self.timeout,
                           write_timeout=self.timeout) as connection:
            connection.reset_input_buffer()
            connection.write(request)
            connection.flush()

            response = bytearray()
            deadline = time.monotonic() + self.timeout
            expected = None
            while time.monotonic() < deadline:
                waiting = connection.in_waiting
                chunk = connection.read(waiting if waiting else 1)
                if chunk:
                    response.extend(chunk)
                    if len(response) >= 2 and response[1] & 0x80:
                        expected = 5
                    elif len(response) >= 3:
                        if response[1] in (FUNCTION_READ_COILS,
                                           FUNCTION_READ_DISCRETE_INPUTS,
                                           FUNCTION_READ_HOLDING_REGISTERS,
                                           FUNCTION_READ_INPUT_REGISTERS):
                            expected = 5 + response[2]
                        else:
                            expected = 8
                    if expected is not None and len(response) >= expected:
                        break

        if not response:
            raise ModbusError("no response (wrong port/slave, disconnected USB, or broadcast request)")
        if len(response) != expected:
            raise ModbusError(f"short response: {response.hex(' ')}")
        if crc16(response[:-2]) != int.from_bytes(response[-2:], "little"):
            raise ModbusError(f"CRC error: {response.hex(' ')}")
        if response[0] != self.slave:
            raise ModbusError(f"unexpected slave address {response[0]}")
        if response[1] not in (request[1], request[1] | 0x80):
            raise ModbusError(f"unexpected response function 0x{response[1]:02X}")
        if response[1] & 0x80:
            detail = " (device busy: PWM channel/timer resources exhausted)" if response[2] == 6 else ""
            raise ModbusError(f"Modbus exception 0x{response[2]:02X}{detail}")
        return bytes(response)

    def read_bits(self, function: int, start: int, count: int) -> list[bool]:
        if function not in (FUNCTION_READ_COILS, FUNCTION_READ_DISCRETE_INPUTS) or not 1 <= count <= 2000:
            raise ValueError("invalid bit-read function or count")
        response = self.transact(fixed_request(self.slave, function, start, count))
        if response[1] != function:
            raise ModbusError("unexpected bit-read response function")
        return decode_bits(response, count)

    def read_registers(self, function: int, start: int, count: int) -> list[int]:
        if function not in (FUNCTION_READ_HOLDING_REGISTERS, FUNCTION_READ_INPUT_REGISTERS) or not 1 <= count <= 125:
            raise ValueError("invalid register-read function or count")
        response = self.transact(fixed_request(self.slave, function, start, count))
        if response[1] != function or response[2] != 2 * count:
            raise ModbusError("register response function or count does not match request")
        return decode_registers(response)

    def write_registers(self, start: int, values: list[int]) -> None:
        if not 1 <= len(values) <= 123 or any(not 0 <= value <= 0xFFFF for value in values):
            raise ValueError("write requires 1..123 unsigned 16-bit registers")
        header = fixed_request(self.slave, FUNCTION_WRITE_MULTIPLE_REGISTERS, start, len(values))[:-2]
        request = add_crc(header + bytes((2 * len(values),)) + b"".join(value.to_bytes(2, "big") for value in values))
        if self.transact(request) != add_crc(header):
            raise ModbusError("write-registers echo did not match the address/count")

    def read_pwm(self, channel: int) -> PwmConfig:
        values = self.read_registers(FUNCTION_READ_HOLDING_REGISTERS, pwm_address(channel), PWM_RECORD_SIZE)
        frequency = (values[0] << 16) | values[1]
        if not PWM_MIN_FREQUENCY <= frequency <= PWM_MAX_FREQUENCY or values[2] > 10000 or values[3] not in (0, 1):
            raise ModbusError("invalid PWM configuration in device response")
        return PwmConfig(frequency, values[2], bool(values[3]))

    def set_pwm(self, channel: int, frequency: int, duty_percent: str | float | Decimal,
                enabled: bool = True) -> None:
        address = pwm_address(channel)
        if isinstance(frequency, bool) or not isinstance(frequency, int) or not PWM_MIN_FREQUENCY <= frequency <= PWM_MAX_FREQUENCY:
            raise ValueError("PWM frequency must be an integer in the range 10..100000 Hz")
        if not isinstance(enabled, bool):
            raise ValueError("PWM enabled must be true or false")
        duty_bp = duty_to_basis_points(duty_percent)
        self.write_registers(address, [frequency >> 16, frequency & 0xFFFF, duty_bp, int(enabled)])

    def stop_pwm(self, channel: int) -> None:
        config = self.read_pwm(channel)
        self.set_pwm(channel, config.frequency, config.duty_percent, False)

    def write_coil(self, channel: int, state: bool) -> None:
        request = fixed_request(self.slave, FUNCTION_WRITE_SINGLE_COIL,
                                channel, 0xFF00 if state else 0x0000)
        response = self.transact(request)
        if response != request:
            raise ModbusError("write-coil echo did not match the request")

    def set_mode(self, channel: int, mode: int) -> None:
        request = fixed_request(self.slave, FUNCTION_WRITE_SINGLE_REGISTER,
                                channel, mode)
        response = self.transact(request)
        if response != request:
            raise ModbusError("set-mode echo did not match the request")


def parse_state(text: str) -> bool:
    normalized = text.lower()
    if normalized in {"1", "on", "high", "true"}:
        return True
    if normalized in {"0", "off", "low", "false"}:
        return False
    raise argparse.ArgumentTypeError("state must be on/off, high/low, true/false, or 1/0")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True, help="USB CDC serial port, for example COM8")
    parser.add_argument("--slave", type=int, default=1, help="Modbus slave address (default: 1)")
    parser.add_argument("--timeout", type=float, default=0.5, help="response timeout in seconds")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name in ("read-inputs", "read-outputs", "read-modes", "read-map"):
        command = subparsers.add_parser(name)
        command.add_argument("start", type=int)
        command.add_argument("count", type=int)

    analog = subparsers.add_parser("read-analog")
    analog.add_argument("start", type=int, help="analog channel, 0 maps to GPIO1")
    analog.add_argument("count", type=int)
    analog.add_argument("--raw", action="store_true", help="return 12-bit raw values instead of mV")

    output = subparsers.add_parser("write-output")
    output.add_argument("channel", type=int)
    output.add_argument("state", type=parse_state)

    mode = subparsers.add_parser("set-mode")
    mode.add_argument("channel", type=int)
    mode.add_argument("mode", type=int, choices=range(6),
                      help="0=floating, 1=pull-up, 2=pull-down, 3=output, 4=analog, 5=PWM (saved settings)")

    pwm = subparsers.add_parser("pwm-set", help="atomically configure frequency, duty and enable")
    pwm.add_argument("channel", type=int)
    pwm.add_argument("frequency", type=int, help="10..100000 Hz")
    pwm.add_argument("duty", help="0..100 percent, up to two decimal places")
    pwm.add_argument("--disabled", action="store_true", help="store settings without starting PWM")
    for name in ("pwm-read", "pwm-stop"):
        command = subparsers.add_parser(name)
        command.add_argument("channel", type=int)

    subparsers.add_parser("info")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    client = Client(args.port, args.slave, args.timeout)
    try:
        if args.command == "read-inputs":
            values = client.read_bits(FUNCTION_READ_DISCRETE_INPUTS, args.start, args.count)
            for channel, value in enumerate(values, args.start):
                print(f"DI[{channel}]={int(value)}")
        elif args.command == "read-outputs":
            values = client.read_bits(FUNCTION_READ_COILS, args.start, args.count)
            for channel, value in enumerate(values, args.start):
                print(f"DO[{channel}]={int(value)}")
        elif args.command == "write-output":
            client.write_coil(args.channel, args.state)
            print(f"DO[{args.channel}]={int(args.state)}")
        elif args.command == "read-analog":
            base = 0x0000 if args.raw else 0x0100
            values = client.read_registers(FUNCTION_READ_INPUT_REGISTERS,
                                           base + args.start, args.count)
            unit = "raw" if args.raw else "mV"
            for channel, value in enumerate(values, args.start):
                rendered = "unavailable" if (not args.raw and value == 0xFFFF) else str(value)
                print(f"AI[{channel}]={rendered} {unit}")
        elif args.command == "read-modes":
            values = client.read_registers(FUNCTION_READ_HOLDING_REGISTERS,
                                           args.start, args.count)
            for channel, value in enumerate(values, args.start):
                print(f"MODE[{channel}]={value}")
        elif args.command == "set-mode":
            client.set_mode(args.channel, args.mode)
            print(f"MODE[{args.channel}]={args.mode}")
        elif args.command == "read-map":
            values = client.read_registers(FUNCTION_READ_HOLDING_REGISTERS,
                                           0x0100 + args.start, args.count)
            for channel, value in enumerate(values, args.start):
                print(f"CHANNEL[{channel}]=GPIO{value}")
        elif args.command in ("pwm-set", "pwm-read", "pwm-stop"):
            if args.command == "pwm-set":
                client.set_pwm(args.channel, args.frequency, args.duty, not args.disabled)
            elif args.command == "pwm-stop":
                client.stop_pwm(args.channel)
            config = client.read_pwm(args.channel)
            print(f"PWM[{args.channel}] frequency={config.frequency} Hz duty={config.duty_percent}% enabled={config.enabled}")
        elif args.command == "info":
            values = client.read_registers(FUNCTION_READ_HOLDING_REGISTERS, 0x0200, 5)
            print(f"protocol={values[0] >> 8}.{values[0] & 0xFF}")
            print(f"firmware={values[1] >> 8}.{values[1] & 0xFF}")
            print(f"digital_channels={values[2]}")
            print(f"analog_channels={values[3]}")
            print(f"calibration_available={bool(values[4] & 1)}")
            print(f"gpio35_37_enabled={bool(values[4] & 2)}")
            print(f"pwm_supported={bool(values[4] & 4)}")
            if values[4] & 4:
                limits = client.read_registers(FUNCTION_READ_HOLDING_REGISTERS, 0x0205, 2)
                print(f"pwm_max_channels={limits[0]}")
                print(f"pwm_max_frequencies={limits[1]}")
        return 0
    except (ModbusError, OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
