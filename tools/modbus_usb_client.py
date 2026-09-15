#!/usr/bin/env python3
"""Small Modbus RTU client for the ESP32-S3 USB CDC IO firmware."""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass


FUNCTION_READ_COILS = 0x01
FUNCTION_READ_DISCRETE_INPUTS = 0x02
FUNCTION_READ_HOLDING_REGISTERS = 0x03
FUNCTION_READ_INPUT_REGISTERS = 0x04
FUNCTION_WRITE_SINGLE_COIL = 0x05
FUNCTION_WRITE_SINGLE_REGISTER = 0x06


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
    byte_count = response[2]
    packed = response[3:3 + byte_count]
    return [bool((packed[index // 8] >> (index % 8)) & 1)
            for index in range(quantity)]


def decode_registers(response: bytes) -> list[int]:
    byte_count = response[2]
    payload = response[3:3 + byte_count]
    if byte_count % 2:
        raise ModbusError("register response has an odd byte count")
    return [int.from_bytes(payload[index:index + 2], "big")
            for index in range(0, len(payload), 2)]


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
        if response[1] & 0x80:
            raise ModbusError(f"Modbus exception 0x{response[2]:02X}")
        return bytes(response)

    def read_bits(self, function: int, start: int, count: int) -> list[bool]:
        response = self.transact(fixed_request(self.slave, function, start, count))
        return decode_bits(response, count)

    def read_registers(self, function: int, start: int, count: int) -> list[int]:
        response = self.transact(fixed_request(self.slave, function, start, count))
        return decode_registers(response)

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
    mode.add_argument("mode", type=int, choices=range(5),
                      help="0=floating, 1=pull-up, 2=pull-down, 3=output, 4=analog")

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
        elif args.command == "info":
            values = client.read_registers(FUNCTION_READ_HOLDING_REGISTERS, 0x0200, 5)
            print(f"protocol={values[0] >> 8}.{values[0] & 0xFF}")
            print(f"firmware={values[1] >> 8}.{values[1] & 0xFF}")
            print(f"digital_channels={values[2]}")
            print(f"analog_channels={values[3]}")
            print(f"calibration_available={bool(values[4] & 1)}")
            print(f"gpio35_37_enabled={bool(values[4] & 2)}")
        return 0
    except (ModbusError, OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
