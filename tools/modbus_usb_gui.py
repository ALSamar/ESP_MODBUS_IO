#!/usr/bin/env python3
"""Python desktop GUI for the ESP32-S3 USB Modbus IO firmware."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
from io import StringIO
import re
import threading
from pathlib import Path
from typing import Any, Callable

import serial
import serial.tools.list_ports
import webview

from modbus_usb_client import (
    Client,
    FUNCTION_READ_COILS,
    FUNCTION_READ_DISCRETE_INPUTS,
    FUNCTION_READ_HOLDING_REGISTERS,
    FUNCTION_READ_INPUT_REGISTERS,
    PwmConfig,
    PWM_MODE,
    DBG_I2C_DISABLE, DBG_SPI_DISABLE, DBG_UART_DISABLE,
)


MODE_NAMES = ("浮空输入", "上拉输入", "下拉输入", "数字输出", "模拟输入", "PWM 输出",
              "I²C 占用", "SPI 占用", "UART 占用")


class Api:
    """Local Python API exposed to the embedded webview."""

    def __init__(self, preferred_port: str = "") -> None:
        self.preferred_port = preferred_port
        self._lock = threading.Lock()
        self._terminal_lock = threading.Lock()
        self._terminal: serial.Serial | None = None

    @staticmethod
    def _port_number(name: str) -> int:
        match = re.search(r"(\d+)$", name)
        return int(match.group(1)) if match else -1

    def get_ports(self) -> dict[str, Any]:
        ports = sorted(
            serial.tools.list_ports.comports(),
            key=lambda item: self._port_number(item.device),
        )
        rows = [
            {
                "device": item.device,
                "description": item.description or "串口设备",
                "vid": item.vid,
                "pid": item.pid,
            }
            for item in ports
        ]
        devices = [item["device"] for item in rows]
        native = next(
            (
                item["device"]
                for item in rows
                if item["vid"] == 0x303A and item["pid"] == 0x4001
            ),
            "",
        )
        recommended = (
            self.preferred_port
            if self.preferred_port in devices
            else native or (devices[-1] if devices else "")
        )
        return {"ok": True, "ports": rows, "recommended": recommended}

    @staticmethod
    def _client(port: str, slave: int) -> Client:
        if not port:
            raise ValueError("请选择 USB CDC 串口")
        if not 1 <= int(slave) <= 247:
            raise ValueError("从站地址必须为 1～247")
        return Client(port, int(slave), timeout=2.0)

    def _run(self, action: Callable[[], Any]) -> dict[str, Any]:
        try:
            with self._lock:
                return {"ok": True, "data": action()}
        except Exception as error:
            return {"ok": False, "error": str(error)}

    def get_info(self, port: str, slave: int) -> dict[str, Any]:
        return self._run(lambda: self._device_info(self._client(port, slave)))

    @staticmethod
    def _device_info(client: Client) -> dict[str, Any]:
        values = client.read_registers(FUNCTION_READ_HOLDING_REGISTERS, 0x0200, 5)
        if not 1 <= values[2] <= 34 or not 0 <= values[3] <= 18:
            raise ValueError("设备返回了不支持的通道数量")
        pwm = bool(values[4] & 4)
        limits = client.read_registers(FUNCTION_READ_HOLDING_REGISTERS, 0x0205, 2) if pwm else [0, 0]
        return {
            "protocol": f"{values[0] >> 8}.{values[0] & 0xFF}",
            "firmware": f"{values[1] >> 8}.{values[1] & 0xFF}",
            "digital": values[2],
            "usable_digital": values[2] if values[4] & 2 else min(values[2], 31),
            "analog": values[3],
            "calibration": bool(values[4] & 1),
            "gpio35_37": bool(values[4] & 2),
            "pwm": pwm,
            "debugger": bool(values[4] & 8),
            "spi": bool(values[4] & 16),
            "uart": bool(values[4] & 32),
            "pwm_channels": limits[0],
            "pwm_frequencies": limits[1],
        }

    @staticmethod
    def _check_channel(channel: int, info: dict[str, Any]) -> int:
        if isinstance(channel, bool) or not isinstance(channel, int) or not 0 <= channel < info["usable_digital"]:
            raise ValueError(f"数字通道必须为 0～{info['usable_digital'] - 1}")
        return channel

    def read_digital(self, port: str, slave: int) -> dict[str, Any]:
        def action() -> list[dict[str, Any]]:
            client = self._client(port, slave)
            count = self._device_info(client)["usable_digital"]
            modes = client.read_registers(
                FUNCTION_READ_HOLDING_REGISTERS, 0, count
            )
            mapping = client.read_registers(
                FUNCTION_READ_HOLDING_REGISTERS, 0x0100, count
            )
            outputs = client.read_bits(FUNCTION_READ_COILS, 0, count)
            inputs: list[bool | None] = [None] * count

            # Analog-mode channels reject digital reads. Read only digital spans
            # so refreshing the table does not silently change any GPIO mode.
            start = 0
            while start < count:
                while start < count and modes[start] >= 4:
                    start += 1
                if start >= count:
                    break
                end = start
                while end < count and modes[end] < 4:
                    end += 1
                inputs[start:end] = client.read_bits(
                    FUNCTION_READ_DISCRETE_INPUTS, start, end - start
                )
                start = end

            return [
                {
                    "channel": index,
                    "gpio": mapping[index],
                    "mode": modes[index],
                    "mode_name": (
                        MODE_NAMES[modes[index]]
                        if modes[index] < len(MODE_NAMES)
                        else "未知"
                    ),
                    "input": inputs[index],
                    "output": outputs[index] if modes[index] == 3 else None,
                }
                for index in range(count)
            ]

        return self._run(action)

    def read_analog(self, port: str, slave: int) -> dict[str, Any]:
        def action() -> list[dict[str, Any]]:
            client = self._client(port, slave)
            count = self._device_info(client)["analog"]
            # GPIO1..18 have digital channels 1..18. Preserve active outputs.
            modes = client.read_registers(FUNCTION_READ_HOLDING_REGISTERS, 1, count)
            raw: list[int | None] = [None] * count
            millivolts: list[int | None] = [None] * count
            start = 0
            while start < count:
                if modes[start] == 3 or modes[start] >= PWM_MODE:
                    start += 1
                    continue
                end = start + 1
                while end < count and modes[end] != 3 and modes[end] < PWM_MODE:
                    end += 1
                raw[start:end] = client.read_registers(FUNCTION_READ_INPUT_REGISTERS, start, end - start)
                millivolts[start:end] = client.read_registers(FUNCTION_READ_INPUT_REGISTERS, 0x0100 + start, end - start)
                start = end
            return [
                {
                    "channel": index,
                    "gpio": index + 1,
                    "raw": raw[index],
                    "state": MODE_NAMES[modes[index]] + "占用" if modes[index] == 3 else MODE_NAMES[modes[index]] if modes[index] >= PWM_MODE else "模拟输入",
                    "millivolts": (
                        None if millivolts[index] == 0xFFFF else millivolts[index]
                    ),
                }
                for index in range(count)
            ]

        return self._run(action)

    def set_mode(
        self, port: str, slave: int, channel: int, mode: int
    ) -> dict[str, Any]:
        def action() -> dict[str, int]:
            client = self._client(port, slave)
            info = self._device_info(client)
            self._check_channel(channel, info)
            if not isinstance(mode, int) or not 0 <= mode <= (5 if info["pwm"] else 4):
                raise ValueError("设备不支持此模式")
            client.set_mode(channel, mode)
            return {"channel": int(channel), "mode": int(mode)}

        return self._run(action)

    def write_output(
        self, port: str, slave: int, channel: int, state: bool
    ) -> dict[str, Any]:
        def action() -> dict[str, Any]:
            client = self._client(port, slave)
            self._check_channel(channel, self._device_info(client))
            client.write_coil(channel, bool(state))
            return {"channel": int(channel), "state": bool(state)}

        return self._run(action)

    @staticmethod
    def _pwm_row(channel: int, config: PwmConfig) -> dict[str, Any]:
        return {"channel": channel, "frequency": config.frequency,
                "duty": config.duty_percent, "enabled": config.enabled}

    def read_pwm(self, port: str, slave: int) -> dict[str, Any]:
        def action() -> list[dict[str, Any]]:
            client = self._client(port, slave)
            info = self._device_info(client)
            if not info["pwm"]:
                raise ValueError("当前固件不支持 PWM，请先更新到协议 1.1")
            count = info["usable_digital"]
            mapping = client.read_registers(FUNCTION_READ_HOLDING_REGISTERS, 0x0100, count)
            rows = []
            for channel in range(count):
                row = self._pwm_row(channel, client.read_pwm(channel))
                row["gpio"] = mapping[channel]
                rows.append(row)
            return rows
        return self._run(action)

    def configure_pwm(self, port: str, slave: int, channel: int, frequency: str,
                      duty: str, enabled: bool) -> dict[str, Any]:
        def action() -> dict[str, Any]:
            client = self._client(port, slave)
            info = self._device_info(client)
            self._check_channel(channel, info)
            if not info["pwm"]:
                raise ValueError("当前固件不支持 PWM，请先更新到协议 1.1")
            if not re.fullmatch(r"[0-9]+", str(frequency)):
                raise ValueError("频率必须为 10～100000 Hz 的整数")
            client.set_pwm(channel, int(frequency), duty, enabled)
            return self._pwm_row(channel, client.read_pwm(channel))
        return self._run(action)

    def stop_pwm(self, port: str, slave: int, channel: int) -> dict[str, Any]:
        def action() -> dict[str, Any]:
            client = self._client(port, slave)
            info = self._device_info(client)
            self._check_channel(channel, info)
            if not info["pwm"]:
                raise ValueError("当前固件不支持 PWM，请先更新到协议 1.1")
            client.stop_pwm(channel)
            return self._pwm_row(channel, client.read_pwm(channel))
        return self._run(action)

    def bus_status(self, port: str, slave: int) -> dict[str, Any]:
        return self._run(lambda: self._client(port, slave).debugger_status())

    def bus_config(self, port: str, slave: int, kind: str,
                   options: dict[str, Any]) -> dict[str, Any]:
        def action() -> dict[str, Any]:
            client = self._client(port, slave)
            if kind == "i2c":
                client.i2c_config(int(options["sda"]), int(options["scl"]), int(options["hz"]))
            elif kind == "spi":
                optional = lambda value: None if value in (None, "", "none") else int(value)
                client.spi_config(int(options["sclk"]), int(options["mosi"]),
                                  optional(options.get("miso")), optional(options.get("cs")),
                                  int(options["mode"]), int(options["hz"]))
            elif kind == "uart":
                client.uart_config(int(options["tx"]), int(options["rx"]),
                                   int(options["baud"]), int(options["data_bits"]),
                                   int(options["parity"]), int(options["stop_bits"]))
            else:
                raise ValueError("未知总线类型")
            return client.debugger_status()
        return self._run(action)

    def bus_disable(self, port: str, slave: int, kind: str) -> dict[str, Any]:
        def action() -> dict[str, Any]:
            op = {"i2c": DBG_I2C_DISABLE, "spi": DBG_SPI_DISABLE,
                  "uart": DBG_UART_DISABLE}.get(kind)
            if op is None:
                raise ValueError("未知总线类型")
            client = self._client(port, slave)
            client.debugger(op)
            return client.debugger_status()
        return self._run(action)

    def i2c_scan(self, port: str, slave: int) -> dict[str, Any]:
        return self._run(lambda: self._client(port, slave).i2c_scan())

    def bus_transfer(self, port: str, slave: int, kind: str,
                     address: int, tx_hex: str, rx_count: int) -> dict[str, Any]:
        def action() -> dict[str, Any]:
            tx = bytes.fromhex(tx_hex)
            client = self._client(port, slave)
            if kind == "i2c":
                received = client.i2c_transfer(int(address), tx, int(rx_count))
            elif kind == "spi":
                received = client.spi_transfer(tx)
            else:
                raise ValueError("未知总线类型")
            return {"rx_hex": received.hex(" ").upper(), "rx_count": len(received)}
        return self._run(action)

    def sample_waveform(self, port: str, slave: int, signals: list[str]) -> dict[str, Any]:
        def action() -> dict[str, Any]:
            if not 1 <= len(signals) <= 4 or len(set(signals)) != len(signals):
                raise ValueError("请选择 1～4 个不同信号")
            result = {}
            with self._client(port, slave) as client:
                for signal in signals:
                    if not re.fullmatch(r"[DA](?:[0-9]|[12][0-9]|3[0-3])", signal):
                        raise ValueError("信号格式必须为 D0～D33 或 A0～A17")
                    channel = int(signal[1:])
                    if signal[0] == "A":
                        if channel >= 18:
                            raise ValueError("模拟信号必须为 A0～A17")
                        mode = client.read_registers(FUNCTION_READ_HOLDING_REGISTERS, channel + 1, 1)[0]
                        if mode == 3 or mode >= 5:
                            raise ValueError(f"{signal} 引脚正被输出或总线占用")
                        result[signal] = client.read_registers(FUNCTION_READ_INPUT_REGISTERS, channel, 1)[0]
                    else:
                        mode = client.read_registers(FUNCTION_READ_HOLDING_REGISTERS, channel, 1)[0]
                        if mode == 4 or mode >= 6:
                            raise ValueError(f"{signal} 当前不能数字采样")
                        result[signal] = int(client.read_bits(FUNCTION_READ_DISCRETE_INPUTS, channel, 1)[0])
            return result
        return self._run(action)

    def terminal_open(self, port: str, baud: int, data_bits: int,
                      parity: str, stop_bits: float) -> dict[str, Any]:
        def action() -> dict[str, Any]:
            if not port or not 300 <= int(baud) <= 2_000_000 or int(data_bits) not in (7, 8) \
                    or parity not in ("N", "E", "O") or float(stop_bits) not in (1, 1.5, 2):
                raise ValueError("串口参数无效")
            with self._terminal_lock:
                if self._terminal is not None:
                    self._terminal.close()
                self._terminal = serial.Serial(port, baudrate=int(baud), bytesize=int(data_bits),
                                               parity=parity, stopbits=float(stop_bits),
                                               timeout=0, write_timeout=1)
            return {"port": port}
        return self._run(action)

    def terminal_close(self) -> dict[str, Any]:
        def action() -> bool:
            with self._terminal_lock:
                if self._terminal is not None:
                    self._terminal.close()
                    self._terminal = None
            return True
        return self._run(action)

    def terminal_read(self) -> dict[str, Any]:
        def action() -> dict[str, Any]:
            with self._terminal_lock:
                if self._terminal is None:
                    return {"open": False, "hex": ""}
                data = self._terminal.read(min(self._terminal.in_waiting, 4096))
                return {"open": True, "hex": data.hex(" ").upper()}
        return self._run(action)

    def terminal_send(self, value: str, hex_mode: bool, ending: str) -> dict[str, Any]:
        def action() -> dict[str, Any]:
            if ending not in ("", "CR", "LF", "CRLF"):
                raise ValueError("行尾格式无效")
            data = bytes.fromhex(value) if hex_mode else value.encode("utf-8")
            data += {"": b"", "CR": b"\r", "LF": b"\n", "CRLF": b"\r\n"}[ending]
            if not data or len(data) > 4096:
                raise ValueError("发送内容必须为 1～4096 字节")
            with self._terminal_lock:
                if self._terminal is None:
                    raise ValueError("请先打开串口")
                count = self._terminal.write(data)
                if count != len(data):
                    raise IOError("串口写入不完整")
            return {"hex": data.hex(" ").upper(), "count": count}
        return self._run(action)

    def save_capture(self, kind: str, rows: list[list[Any]] | str) -> dict[str, Any]:
        def action() -> str:
            if kind not in ("waveform", "terminal"):
                raise ValueError("未知导出类型")
            name = f"{kind}_{datetime.now():%Y%m%d_%H%M%S}." + ("csv" if kind == "waveform" else "txt")
            path = webview.windows[0].create_file_dialog(webview.SAVE_DIALOG, save_filename=name)
            if not path:
                return ""
            if isinstance(path, (tuple, list)):
                path = path[0]
            if kind == "waveform":
                if not isinstance(rows, list) or len(rows) > 5000:
                    raise ValueError("波形数据过长")
                output = StringIO()
                writer = csv.writer(output)
                writer.writerows(rows)
                content = output.getvalue()
            else:
                content = str(rows)
            Path(path).write_text(content, encoding="utf-8-sig")
            return str(path)
        return self._run(action)


HTML = r"""
<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ESP32-S3 USB Modbus IO</title>
<style>
:root{--nav:#18324f;--nav2:#28577f;--blue:#287fc1;--green:#16825a;--red:#be4646;--amber:#b8751a;--bg:#f3f6fa;--line:#dbe4ed;--text:#203247;--muted:#6d7d8e}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px "Microsoft YaHei UI","Segoe UI",sans-serif;overflow:hidden}
header{height:78px;padding:15px 24px;background:linear-gradient(110deg,var(--nav),var(--nav2));color:white;display:flex;align-items:center;justify-content:space-between}
h1{margin:0;font-size:23px;letter-spacing:.7px}header p{margin:4px 0 0;color:#c8daea}.badge{padding:8px 15px;border:1px solid #6384a3;border-radius:20px;color:#d7e5f1}
.connect{height:66px;padding:14px 22px;background:white;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:10px}.small{font-size:13px;color:var(--muted)}
select,input,button{height:34px;border:1px solid #b9c7d5;border-radius:5px;background:white;color:var(--text);padding:0 11px;font:inherit}select:focus,input:focus{outline:none;border-color:var(--blue);box-shadow:0 0 0 2px #d7ebfb}
button{cursor:pointer}button:hover{background:#eff5fa}button:disabled{opacity:.55;cursor:wait}button.primary{background:var(--blue);border-color:var(--blue);color:white;font-weight:600}button.high{background:#def5e9;border-color:#9bd7ba;color:#116949}button.low{background:#fbe5e5;border-color:#e6adad;color:#a63636}
#port{min-width:210px}#slave,#channel{width:72px}#status{margin-left:auto;color:var(--muted);white-space:nowrap}#status.good{color:var(--green)}#status.bad{color:var(--red)}#status.busy{color:var(--amber)}
nav{height:48px;padding:0 22px;background:white;border-bottom:1px solid var(--line);display:flex;gap:3px}nav button{height:48px;border:0;border-radius:0;background:transparent;padding:0 22px;color:var(--muted);border-bottom:3px solid transparent}nav button.active{color:var(--blue);border-bottom-color:var(--blue);font-weight:600}
main{height:calc(100vh - 192px);padding:20px 22px;overflow:hidden}.page{display:none;height:100%}.page.active{display:block}.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}.card{min-height:112px;padding:17px 19px;background:white;border:1px solid var(--line);border-radius:9px;box-shadow:0 2px 7px #1c344b0d}.caption{color:var(--muted);font-size:13px}.value{margin-top:9px;font-size:26px;font-weight:700;color:#193754}.notice{margin-top:18px;padding:16px 18px;background:#e8f3fc;border:1px solid #c6dff3;border-radius:8px;color:#356887;line-height:1.7}
.toolbar{height:54px;display:flex;align-items:center;gap:10px;margin-bottom:12px}.spacer{flex:1}#mode{min-width:132px}.tablebox{height:calc(100% - 66px);background:white;border:1px solid var(--line);border-radius:8px;overflow:auto}table{width:100%;border-collapse:collapse;table-layout:fixed}th{position:sticky;top:0;z-index:1;background:#edf3f8;color:#536578;text-align:left}th,td{padding:10px 14px;border-bottom:1px solid #e5ebf1}tbody tr:hover{background:#f3f9fd}tbody tr.selected{background:#e1f0fc}.level{display:inline-block;min-width:42px;padding:3px 9px;border-radius:12px;text-align:center;font-size:12px}.on{color:#08704c;background:#dff5e9}.off{color:#68798a;background:#edf1f5}.na{color:#8b7852;background:#f7efdd}.empty{text-align:center;color:var(--muted);padding:35px}
#log{width:100%;height:100%;resize:none;border:1px solid var(--line);border-radius:8px;background:#172431;color:#d8e5f1;padding:15px;font:13px/1.55 Consolas,monospace}
.pwm-controls{display:flex;flex-wrap:wrap;flex-shrink:0;gap:12px;align-items:center;padding:14px 16px;background:white;border:1px solid var(--line);border-radius:8px}.pwm-controls label{display:flex;align-items:center;gap:7px}.pwm-controls input[type=number]{width:112px}.pwm-controls input[type=checkbox]{height:auto}#pwm.active{display:flex;flex-direction:column}#pwm-note{flex-shrink:0;margin:12px 0;line-height:1.6;color:var(--muted)}#pwm .tablebox{height:auto;flex:1;min-height:0}
.tool-page{overflow:auto}.tool-grid{display:grid;grid-template-columns:repeat(2,minmax(350px,1fr));gap:14px}.tool-card{background:white;border:1px solid var(--line);border-radius:8px;padding:15px;margin-bottom:14px}.tool-card h2{font-size:16px;margin:0 0 12px}.tool-card p{line-height:1.6;color:var(--muted);margin:9px 0}.fields{display:flex;flex-wrap:wrap;gap:9px;align-items:center}.fields label{display:flex;align-items:center;gap:5px}.fields input[type=number]{width:92px}.fields input.wide{width:180px}.fields select{min-width:74px}.result{font:13px/1.6 Consolas,monospace;white-space:pre-wrap;word-break:break-all;background:#f0f5f9;padding:10px;border-radius:6px;min-height:38px}
#terminal-output{height:calc(100% - 170px);min-height:220px;overflow:auto;background:#172431;color:#d8e5f1;border-radius:7px;padding:14px;font:13px/1.5 Consolas,monospace;white-space:pre-wrap;word-break:break-all}.terminal-send{width:100%;margin-top:10px;display:flex;gap:8px}.terminal-send input{flex:1}#terminal.active{display:flex;flex-direction:column;overflow:auto}#terminal .toolbar{height:auto;min-height:48px;flex-wrap:wrap}
#waveform.active{display:flex;flex-direction:column;overflow:auto}#wave-canvas{width:100%;height:calc(100% - 132px);min-height:280px;background:white;border:1px solid var(--line);border-radius:8px}#wave-legend{height:32px;display:flex;gap:20px;align-items:center;color:var(--muted)}
</style></head><body>
<header><div><h1>ESP32-S3 · MODBUS IO</h1><p>Python USB CDC 设备调试与通道控制</p></div><div class="badge">Modbus RTU · USB CDC</div></header>
<section class="connect"><span class="small">串口</span><select id="port"></select><button onclick="loadPorts()">刷新端口</button><span class="small" style="margin-left:10px">从站</span><input id="slave" type="number" min="1" max="247" value="1"><button class="primary" onclick="connectDevice()">连接并读取</button><span id="status">尚未连接</span></section>
<nav><button class="active" data-page="info" onclick="showPage(this)">设备信息</button><button data-page="digital" onclick="showPage(this)">数字 IO</button><button data-page="analog" onclick="showPage(this)">模拟输入</button><button data-page="pwm" onclick="showPage(this)">PWM 输出</button><button data-page="buses" onclick="showPage(this)">I²C / SPI / UART</button><button data-page="terminal" onclick="showPage(this)">串口助手</button><button data-page="waveform" onclick="showPage(this)">实时波形</button><button data-page="logs" onclick="showPage(this)">日志</button></nav>
<main>
<section id="info" class="page active"><div class="cards">
<div class="card"><div class="caption">协议版本</div><div id="protocol" class="value">—</div></div><div class="card"><div class="caption">固件版本</div><div id="firmware" class="value">—</div></div><div class="card"><div class="caption">数字通道</div><div id="digital-count" class="value">—</div></div>
<div class="card"><div class="caption">模拟通道</div><div id="analog-count" class="value">—</div></div><div class="card"><div class="caption">ADC 校准</div><div id="calibration" class="value">—</div></div><div class="card"><div class="caption">GPIO35–37</div><div id="reserved" class="value">—</div></div></div>
<div class="notice">GPIO19、GPIO20 由原生 USB 占用。GPIO35～37 默认安全禁用。悬空输入会出现随机电平和 ADC 数值。</div></section>
<section id="digital" class="page"><div class="toolbar"><button class="primary" onclick="refreshDigital()">刷新数字 IO</button><span class="spacer"></span><span class="small">通道</span><select id="channel"></select><select id="mode"><option value="0">浮空输入</option><option value="1">上拉输入</option><option value="2">下拉输入</option><option value="3">数字输出</option><option value="4">模拟输入</option></select><button onclick="setMode()">设置模式</button><button class="high" onclick="writeOutput(true)">输出高</button><button class="low" onclick="writeOutput(false)">输出低</button></div>
<div class="tablebox"><table><thead><tr><th>通道</th><th>GPIO</th><th>模式</th><th>输入电平</th><th>输出状态</th></tr></thead><tbody id="digital-body"><tr><td colspan="5" class="empty">点击“刷新数字 IO”读取数据</td></tr></tbody></table></div></section>
<section id="analog" class="page"><div class="toolbar"><button class="primary" onclick="refreshAnalog()">读取可用 ADC</button><span class="small">自动切换可用 GPIO1～18 为模拟输入；保留数字输出和 PWM</span></div><div class="tablebox"><table><thead><tr><th>模拟通道</th><th>GPIO</th><th>ADC 原始值</th><th>校准电压</th></tr></thead><tbody id="analog-body"><tr><td colspan="4" class="empty">点击“读取可用 ADC”获取数据</td></tr></tbody></table></div></section>
<section id="pwm" class="page"><div class="pwm-controls">
<label>通道 <select id="pwm-channel"></select></label><label>频率 <input id="pwm-frequency" type="number" min="10" max="100000" step="1" value="1000"> Hz</label><label>占空比 <input id="pwm-duty" type="number" min="0" max="100" step="0.01" value="50.00"> %</label><label><input id="pwm-enabled" type="checkbox" checked>启用输出</label>
<button data-pwm class="primary" onclick="applyPwm()">应用参数</button><button data-pwm class="low" onclick="stopPwm()">停止所选通道</button><button data-pwm onclick="refreshPwm()">读取 PWM 状态</button></div>
<div id="pwm-note">连接设备后可配置 PWM。频率 10～100000 Hz，占空比 0～100%，步进 0.01%。</div>
<div class="tablebox"><table><thead><tr><th>通道</th><th>GPIO</th><th>设置频率</th><th>设置占空比</th><th>输出状态</th></tr></thead><tbody id="pwm-body"><tr><td colspan="5" class="empty">读取状态后点击通道行，可载入该通道参数</td></tr></tbody></table></div></section>
<section id="buses" class="page tool-page"><div class="tool-grid">
<div><div class="tool-card"><h2>I²C 主机</h2><div class="fields"><label>SDA 通道 <input id="i2c-sda" type="number" min="0" max="33" value="4"></label><label>SCL 通道 <input id="i2c-scl" type="number" min="0" max="33" value="5"></label><label>频率 Hz <input id="i2c-hz" type="number" min="10000" max="400000" value="100000"></label><button class="primary" onclick="configureBus('i2c')">启用</button><button onclick="disableBus('i2c')">释放</button></div><p>需要外部上拉电阻。配置的是数字通道号，不是 GPIO 号；可在数字 IO 页查看映射。</p><div class="fields"><button onclick="scanI2c()">扫描 7 位地址</button><label>地址 <input id="i2c-address" value="0x50" class="wide"></label><label>读字节数 <input id="i2c-rx" type="number" min="0" max="128" value="0"></label></div><div class="fields" style="margin-top:9px"><label>写入 HEX <input id="i2c-tx" class="wide" placeholder="00 01 FF"></label><button onclick="transferBus('i2c')">执行写 / 读 / 写后读</button></div><p id="i2c-result" class="result">尚未执行事务</p></div>
<div class="tool-card"><h2>UART1 ↔ USB CDC1 透传</h2><div class="fields"><label>TX 通道 <input id="uart-tx" type="number" min="0" max="33" value="25"></label><label>RX 通道 <input id="uart-rx" type="number" min="0" max="33" value="26"></label><label>波特率 <input id="uart-baud" type="number" min="300" max="2000000" value="115200"></label></div><div class="fields" style="margin-top:9px"><label>数据位 <select id="uart-data"><option>8</option><option>7</option></select></label><label>校验 <select id="uart-parity"><option value="0">无</option><option value="1">偶</option><option value="2">奇</option></select></label><label>停止位 <select id="uart-stop"><option>1</option><option>2</option></select></label><button class="primary" onclick="configureBus('uart')">启用</button><button onclick="disableBus('uart')">释放</button></div><p>控制命令经 CDC0 发送；目标 UART 原始字节经电脑上出现的第二个 COM 口传输。切到“串口助手”并选择第二个端口。</p></div></div>
<div><div class="tool-card"><h2>SPI 主机</h2><div class="fields"><label>SCLK <input id="spi-sclk" type="number" min="0" max="33" value="6"></label><label>MOSI <input id="spi-mosi" type="number" min="0" max="33" value="7"></label><label>MISO <input id="spi-miso" type="number" min="0" max="33" value="8"></label><label>CS <input id="spi-cs" type="number" min="0" max="33" value="9"></label></div><div class="fields" style="margin-top:9px"><label>模式 <select id="spi-mode"><option>0</option><option>1</option><option>2</option><option>3</option></select></label><label>频率 Hz <input id="spi-hz" type="number" min="10000" max="10000000" value="1000000"></label><button class="primary" onclick="configureBus('spi')">启用</button><button onclick="disableBus('spi')">释放</button></div><p>MISO、CS 可留空；无 CS 时需自行确保目标设备片选。每次事务同步收发相同字节数。</p><div class="fields"><label>发送 HEX <input id="spi-tx" class="wide" placeholder="9F 00 00 00"></label><button onclick="transferBus('spi')">全双工传输</button></div><p id="spi-result" class="result">尚未执行事务</p></div><div class="tool-card"><h2>外设状态</h2><button onclick="refreshBusStatus()">读取状态</button><p id="bus-status" class="result">连接协议 1.2 固件后读取</p><p>总线配置前，请先将所选通道设置为“浮空输入”；释放后回到浮空输入。运行中的数字输出、PWM 和其他总线不会被抢占。</p></div></div></div></section>
<section id="terminal" class="page"><div class="toolbar"><label>端口 <select id="terminal-port"></select></label><button onclick="loadPorts()">刷新端口</button><label>波特率 <input id="terminal-baud" type="number" min="300" max="2000000" value="115200" style="width:100px"></label><select id="terminal-data"><option>8</option><option>7</option></select><select id="terminal-parity"><option value="N">无校验</option><option value="E">偶校验</option><option value="O">奇校验</option></select><select id="terminal-stop"><option>1</option><option>1.5</option><option>2</option></select><button class="primary" onclick="openTerminal()">打开</button><button onclick="closeTerminal()">关闭</button><span id="terminal-state" class="small">未打开</span></div><div class="toolbar"><label><input id="terminal-hex-view" type="checkbox" style="height:auto">HEX 显示</label><label><input id="terminal-stamp" type="checkbox" checked style="height:auto">时间戳</label><label><input id="terminal-scroll" type="checkbox" checked style="height:auto">自动滚动</label><button onclick="clearTerminal()">清空</button><button onclick="saveTerminal()">保存日志</button><span class="small">支持任意系统串口；打开 CDC0 时，请勿同时使用控制页</span></div><pre id="terminal-output"></pre><div class="terminal-send"><input id="terminal-input" placeholder="输入文本或空格分隔的十六进制字节；回车发送"><label><input id="terminal-hex-send" type="checkbox" style="height:auto">HEX 发送</label><select id="terminal-ending"><option value="">无行尾</option><option value="CR">CR</option><option value="LF">LF</option><option value="CRLF">CRLF</option></select><button class="primary" onclick="sendTerminal()">发送</button></div></section>
<section id="waveform" class="page"><div class="toolbar"><label>信号 <input id="wave-signals" class="wide" value="A0,D0" placeholder="A0,D0" style="width:180px"></label><label>间隔 <select id="wave-interval"><option value="250">250 ms</option><option value="500">500 ms</option><option value="1000">1 s</option><option value="2000">2 s</option></select></label><button class="primary" onclick="startWaveform()">开始</button><button onclick="stopWaveform()">停止</button><button onclick="clearWaveform()">清空</button><button onclick="saveWaveform()">导出 CSV</button><span id="wave-state" class="small">D0～D33：数字电平；A0～A17：ADC 原始值；最多 4 路</span></div><div id="wave-legend"></div><canvas id="wave-canvas" width="1100" height="400"></canvas><p class="small">软件轮询波形用于趋势观察，不是逻辑分析仪或示波器；采样速率受 USB 往返和 ADC 测量耗时影响。</p></section>
<section id="logs" class="page"><textarea id="log" readonly></textarea></section></main>
<script>
const $=id=>document.getElementById(id);let busy=0,device=null,pwmRows=[];const stamp=()=>new Date().toLocaleTimeString('zh-CN',{hour12:false});
function log(s){$('log').value+=`[${stamp()}] ${s}\n`;$('log').scrollTop=$('log').scrollHeight}function status(s,k=''){$('status').textContent=s;$('status').className=k}
function working(on,s='正在通信…'){busy=Math.max(0,busy+(on?1:-1));document.querySelectorAll('button').forEach(b=>b.disabled=busy>0||(b.hasAttribute('data-pwm')&&!device?.pwm));document.querySelectorAll('select,input').forEach(x=>x.disabled=busy>0);if(on)status(s,'busy')}
function conn(){return{port:$('port').value,slave:Number($('slave').value)}}function fail(name,r){let s=r?.error||'未知错误';status(`失败：${s}`,'bad');log(`${name}失败：${s}`)}
function showPage(b){document.querySelectorAll('nav button').forEach(x=>x.classList.remove('active'));b.classList.add('active');document.querySelectorAll('.page').forEach(x=>x.classList.remove('active'));$(b.dataset.page).classList.add('active');if(b.dataset.page==='waveform')drawWave()}
function level(v,label='—'){return v===null?`<span class="level na">${label}</span>`:v?'<span class="level on">高</span>':'<span class="level off">低</span>'}
function useInfo(d){device=d;for(let id of ['channel','pwm-channel']){let box=$(id),old=box.value;box.innerHTML='';for(let i=0;i<d.usable_digital;i++){let o=document.createElement('option');o.value=i;o.textContent=i;box.appendChild(o)}if([...box.options].some(o=>o.value===old))box.value=old}let mode=$('mode'),extra=mode.querySelector('[value="5"]');if(d.pwm&&!extra){let o=document.createElement('option');o.value='5';o.textContent='PWM（已存参数）';mode.appendChild(o)}else if(!d.pwm&&extra)extra.remove();$('pwm-note').textContent=d.pwm?`10～100000 Hz；占空比 0～100%，步进 0.01%。最多 ${d.pwm_channels} 路同时输出、${d.pwm_frequencies} 种频率；同频率通道共享定时器。表中显示设定值，实际波形受硬件分辨率量化。参数断电复位，停止后恢复浮空输入。`:'当前固件不支持 PWM；数字 IO 和 ADC 可继续使用。更新到协议 1.1 后可配置 PWM。'}
async function loadPorts(){working(true,'正在扫描串口…');try{let old=$('port').value,terminalOld=$('terminal-port').value,r=await pywebview.api.get_ports(),box=$('port'),tb=$('terminal-port');box.innerHTML='';tb.innerHTML='';r.ports.forEach(p=>{for(let target of [box,tb]){let o=document.createElement('option');o.value=p.device;o.textContent=`${p.device} · ${p.description}`;target.appendChild(o)}});box.value=[...box.options].some(o=>o.value===old)?old:r.recommended;tb.value=[...tb.options].some(o=>o.value===terminalOld)?terminalOld:r.ports.find(p=>p.device!==box.value)?.device||box.value;log(`发现串口：${r.ports.map(p=>p.device).join(', ')||'无'}`);status(r.ports.length?'请选择端口并连接':'未发现串口',r.ports.length?'':'bad')}catch(e){fail('扫描',{error:String(e)})}finally{working(false)}}
async function connectDevice(){let c=conn();device=null;working(true,'正在读取设备信息…');try{let r=await pywebview.api.get_info(c.port,c.slave);if(!r.ok){fail('连接',r);return}let d=r.data;useInfo(d);$('protocol').textContent=d.protocol;$('firmware').textContent=d.firmware;$('digital-count').textContent=`${d.usable_digital} / ${d.digital}`;$('analog-count').textContent=d.analog;$('calibration').textContent=d.calibration?'可用':'不可用';$('reserved').textContent=d.gpio35_37?'已启用':'安全禁用';status(`已连接 · ${c.port} · 从站 ${c.slave}`,'good');log(`连接成功：协议 ${d.protocol}，固件 ${d.firmware}，PWM ${d.pwm?'可用':'不支持'}`)}catch(e){fail('连接',{error:String(e)})}finally{working(false)}}
async function refreshDigital(){let c=conn();working(true,'正在读取数字 IO…');try{let r=await pywebview.api.read_digital(c.port,c.slave);if(!r.ok){fail('数字 IO',r);return}$('digital-body').innerHTML=r.data.map(x=>`<tr onclick="pick(${x.channel},this)"><td>${x.channel}</td><td>GPIO${x.gpio}</td><td>${x.mode_name}</td><td>${level(x.input,x.mode===5?'PWM':x.mode===4?'模拟':'—')}</td><td>${level(x.output,x.mode===5?'PWM':'—')}</td></tr>`).join('');status(`数字 IO 已刷新 · ${c.port}`,'good');log(`${r.data.length} 路数字 IO 读取成功`)}catch(e){fail('数字 IO',{error:String(e)})}finally{working(false)}}
function pick(ch,row){$('channel').value=String(ch);document.querySelectorAll('#digital-body tr').forEach(r=>r.classList.remove('selected'));row.classList.add('selected')}
async function refreshAnalog(){let c=conn();working(true,'正在读取 ADC…');try{let r=await pywebview.api.read_analog(c.port,c.slave);if(!r.ok){fail('ADC',r);return}$('analog-body').innerHTML=r.data.map(x=>`<tr><td>AI${x.channel}</td><td>GPIO${x.gpio}</td><td>${x.raw===null?x.state:x.raw}</td><td>${x.millivolts===null?'不可用':x.millivolts+' mV'}</td></tr>`).join('');status(`ADC 已刷新 · ${c.port}`,'good');log(`${r.data.filter(x=>x.raw!==null).length} 路 ADC 读取成功；输出通道保持不变`)}catch(e){fail('ADC',{error:String(e)})}finally{working(false)}}
async function setMode(){let c=conn(),ch=Number($('channel').value),m=Number($('mode').value);working(true,'正在设置模式…');try{let r=await pywebview.api.set_mode(c.port,c.slave,ch,m);if(!r.ok){fail('设置模式',r);return}log(`通道 ${ch} 设置为 ${$('mode').selectedOptions[0].text}`);await refreshDigital()}catch(e){fail('设置模式',{error:String(e)})}finally{working(false)}}
async function writeOutput(v){let c=conn(),ch=Number($('channel').value);working(true,v?'正在输出高电平…':'正在输出低电平…');try{let r=await pywebview.api.write_output(c.port,c.slave,ch,v);if(!r.ok){fail('数字输出',r);return}log(`通道 ${ch} 输出${v?'高':'低'}电平`);await refreshDigital()}catch(e){fail('数字输出',{error:String(e)})}finally{working(false)}}
function pickPwm(ch){let x=pwmRows.find(x=>x.channel===ch);if(!x)return;$('pwm-channel').value=String(ch);$('pwm-frequency').value=x.frequency;$('pwm-duty').value=x.duty;$('pwm-enabled').checked=x.enabled}
async function refreshPwm(){let c=conn();working(true,'正在读取 PWM 状态…');try{let r=await pywebview.api.read_pwm(c.port,c.slave);if(!r.ok){fail('PWM',r);return}pwmRows=r.data;$('pwm-body').innerHTML=r.data.map(x=>`<tr onclick="pickPwm(${x.channel})"><td>${x.channel}</td><td>GPIO${x.gpio}</td><td>${x.frequency} Hz</td><td>${x.duty}%</td><td><span class="level ${x.enabled?'on':'off'}">${x.enabled?'运行中':'未启用'}</span></td></tr>`).join('');status(`${r.data.filter(x=>x.enabled).length} 路 PWM 运行中`,'good');log('PWM 状态读取成功')}catch(e){fail('PWM',{error:String(e)})}finally{working(false)}}
async function applyPwm(){let c=conn(),ch=Number($('pwm-channel').value);working(true,'正在配置 PWM…');try{let r=await pywebview.api.configure_pwm(c.port,c.slave,ch,$('pwm-frequency').value,$('pwm-duty').value,$('pwm-enabled').checked);if(!r.ok){fail('配置 PWM',r);return}log(`PWM 通道 ${ch}：${r.data.frequency} Hz，${r.data.duty}%，${r.data.enabled?'已启用':'已停用/存储'}`);await refreshPwm()}catch(e){fail('配置 PWM',{error:String(e)})}finally{working(false)}}
async function stopPwm(){let c=conn(),ch=Number($('pwm-channel').value);working(true,'正在停止 PWM…');try{let r=await pywebview.api.stop_pwm(c.port,c.slave,ch);if(!r.ok){fail('停止 PWM',r);return}$('pwm-enabled').checked=false;log(`PWM 通道 ${ch} 已停用，频率和占空比设定保留`);await refreshPwm()}catch(e){fail('停止 PWM',{error:String(e)})}finally{working(false)}}
function requireDebugger(){if(!device?.debugger)throw Error('请连接协议 1.2 固件；旧版设备不支持总线调试')}
function busOptions(kind){if(kind==='i2c')return{sda:$('i2c-sda').value,scl:$('i2c-scl').value,hz:$('i2c-hz').value};if(kind==='spi')return{sclk:$('spi-sclk').value,mosi:$('spi-mosi').value,miso:$('spi-miso').value,cs:$('spi-cs').value,mode:$('spi-mode').value,hz:$('spi-hz').value};return{tx:$('uart-tx').value,rx:$('uart-rx').value,baud:$('uart-baud').value,data_bits:$('uart-data').value,parity:$('uart-parity').value,stop_bits:$('uart-stop').value}}
function showBus(s){$('bus-status').textContent=`I²C: ${s.i2c?`通道 ${s.i2c_pins.join('/')} · ${s.i2c_hz} Hz`:'关闭'}\nSPI: ${s.spi?`通道 ${s.spi_pins.join('/')} · 模式 ${s.spi_mode} · ${s.spi_hz} Hz`:'关闭'}\nUART: ${s.uart?`通道 ${s.uart_pins.join('/')} · ${s.uart_baud} bps · ${s.uart_data_bits}${['N','E','O'][s.uart_parity]}${s.uart_stop_bits}`:'关闭'}`}
async function refreshBusStatus(){let c=conn();working(true,'读取总线状态…');try{requireDebugger();let r=await pywebview.api.bus_status(c.port,c.slave);if(!r.ok){fail('总线状态',r);return}showBus(r.data);status('总线状态已刷新','good')}catch(e){fail('总线状态',{error:String(e)})}finally{working(false)}}
async function configureBus(kind){let c=conn();working(true,`配置 ${kind.toUpperCase()}…`);try{requireDebugger();let r=await pywebview.api.bus_config(c.port,c.slave,kind,busOptions(kind));if(!r.ok){fail('总线配置',r);return}showBus(r.data);log(`${kind.toUpperCase()} 已启用；所用通道已锁定`);status(`${kind.toUpperCase()} 已启用`,'good')}catch(e){fail('总线配置',{error:String(e)})}finally{working(false)}}
async function disableBus(kind){let c=conn();working(true,`释放 ${kind.toUpperCase()}…`);try{requireDebugger();let r=await pywebview.api.bus_disable(c.port,c.slave,kind);if(!r.ok){fail('释放总线',r);return}showBus(r.data);log(`${kind.toUpperCase()} 已释放；引脚恢复浮空输入`);status(`${kind.toUpperCase()} 已释放`,'good')}catch(e){fail('释放总线',{error:String(e)})}finally{working(false)}}
async function scanI2c(){let c=conn();working(true,'正在扫描 I²C…');try{requireDebugger();let r=await pywebview.api.i2c_scan(c.port,c.slave);if(!r.ok){fail('I²C 扫描',r);return}$('i2c-result').textContent=r.data.length?r.data.map(a=>`0x${a.toString(16).toUpperCase().padStart(2,'0')}`).join('  '):'没有发现应答设备';log(`I²C 扫描：${r.data.length} 个地址应答`);status('I²C 扫描完成','good')}catch(e){fail('I²C 扫描',{error:String(e)})}finally{working(false)}}
async function transferBus(kind){let c=conn();working(true,`${kind.toUpperCase()} 事务执行中…`);try{requireDebugger();let tx=$(kind+'-tx').value,addr=kind==='i2c'?Number($('i2c-address').value):0,rx=kind==='i2c'?Number($('i2c-rx').value):0;let r=await pywebview.api.bus_transfer(c.port,c.slave,kind,addr,tx,rx);if(!r.ok){fail('总线事务',r);return}$(kind+'-result').textContent=`RX (${r.data.rx_count} B): ${r.data.rx_hex||'—'}`;log(`${kind.toUpperCase()} TX ${tx||'—'} → RX ${r.data.rx_hex||'—'}`);status(`${kind.toUpperCase()} 事务完成`,'good')}catch(e){fail('总线事务',{error:String(e)})}finally{working(false)}}
let terminalOpen=false,terminalReading=false,terminalDecoder=new TextDecoder('utf-8');
function terminalAppend(direction,hex){if(!hex)return;let bytes=Uint8Array.from(hex.split(' ').map(x=>parseInt(x,16))),body=$('terminal-hex-view').checked?hex:terminalDecoder.decode(bytes,{stream:true});let prefix=$('terminal-stamp').checked?`[${stamp()}] `:'';$('terminal-output').textContent+=`${prefix}${direction} ${body}\n`;if($('terminal-output').textContent.length>150000)$('terminal-output').textContent=$('terminal-output').textContent.slice(-100000);if($('terminal-scroll').checked)$('terminal-output').scrollTop=$('terminal-output').scrollHeight}
async function openTerminal(){try{let r=await pywebview.api.terminal_open($('terminal-port').value,Number($('terminal-baud').value),Number($('terminal-data').value),$('terminal-parity').value,Number($('terminal-stop').value));if(!r.ok){fail('打开串口',r);return}terminalOpen=true;terminalDecoder=new TextDecoder('utf-8');$('terminal-state').textContent=`已打开 ${r.data.port}`;log(`串口助手打开 ${r.data.port}`)}catch(e){fail('打开串口',{error:String(e)})}}
async function closeTerminal(){try{let r=await pywebview.api.terminal_close();if(!r.ok){fail('关闭串口',r);return}terminalOpen=false;$('terminal-state').textContent='未打开';log('串口助手已关闭')}catch(e){fail('关闭串口',{error:String(e)})}}
async function pollTerminal(){if(!terminalOpen||terminalReading)return;terminalReading=true;try{let r=await pywebview.api.terminal_read();if(!r.ok){terminalOpen=false;fail('串口接收',r);$('terminal-state').textContent='接收失败';return}if(!r.data.open){terminalOpen=false;$('terminal-state').textContent='已断开';return}terminalAppend('RX',r.data.hex)}catch(e){terminalOpen=false;fail('串口接收',{error:String(e)})}finally{terminalReading=false}}
async function sendTerminal(){try{let r=await pywebview.api.terminal_send($('terminal-input').value,$('terminal-hex-send').checked,$('terminal-ending').value);if(!r.ok){fail('串口发送',r);return}terminalAppend('TX',r.data.hex);log(`串口发送 ${r.data.count} 字节`)}catch(e){fail('串口发送',{error:String(e)})}}
function clearTerminal(){$('terminal-output').textContent=''}
async function saveTerminal(){let r=await pywebview.api.save_capture('terminal',$('terminal-output').textContent);if(!r.ok)fail('保存串口日志',r);else if(r.data)log(`串口日志已保存：${r.data}`)}
let waveRunning=false,waveSignals=[],waveRows=[],waveTimer=null;const waveColors=['#2781c1','#e59a27','#27a276','#be57a7'];
function waveSelection(){let signals=$('wave-signals').value.split(',').map(x=>x.trim().toUpperCase()).filter(Boolean);if(!signals.length||signals.length>4||new Set(signals).size!==signals.length||signals.some(x=>!/^([DA])([0-9]|[12][0-9]|3[0-3])$/.test(x)||x[0]==='A'&&Number(x.slice(1))>17))throw Error('请填写 1～4 个不重复的 D0～D33 或 A0～A17，逗号分隔');return signals}
function drawWave(){let canvas=$('wave-canvas'),ctx=canvas.getContext('2d'),w=canvas.width,h=canvas.height,left=55,right=w-20,top=20,bottom=h-38;ctx.clearRect(0,0,w,h);ctx.fillStyle='#fff';ctx.fillRect(0,0,w,h);ctx.strokeStyle='#e6edf3';ctx.lineWidth=1;for(let i=0;i<=5;i++){let y=top+i*(bottom-top)/5;ctx.beginPath();ctx.moveTo(left,y);ctx.lineTo(right,y);ctx.stroke()}ctx.fillStyle='#6d7d8e';ctx.font='12px sans-serif';ctx.fillText('最近 '+waveRows.length+' 点',left,bottom+25);let visible=waveRows.slice(-500);waveSignals.forEach((s,index)=>{let vals=visible.map(row=>row.values[s]).filter(Number.isFinite),min=s[0]==='D'?0:Math.min(...vals),max=s[0]==='D'?1:Math.max(...vals);if(!vals.length)return;if(min===max){min-=1;max+=1}ctx.strokeStyle=waveColors[index];ctx.lineWidth=2;ctx.beginPath();visible.forEach((row,i)=>{let x=left+(right-left)*i/Math.max(visible.length-1,1),y=bottom-(row.values[s]-min)/(max-min)*(bottom-top);i?ctx.lineTo(x,y):ctx.moveTo(x,y)});ctx.stroke();let label=`${s}: ${vals[vals.length-1]} (${min.toFixed(0)}～${max.toFixed(0)})`;ctx.fillStyle=waveColors[index];ctx.fillText(label,left+index*245,top+13)});$('wave-legend').textContent=waveRows.length?`采样点 ${waveRows.length} · 当前视窗最多 500 点 · 实际间隔由事务耗时决定`:'暂无采样'}
async function waveTick(){if(!waveRunning)return;let c=conn();try{let r=await pywebview.api.sample_waveform(c.port,c.slave,waveSignals);if(!r.ok){stopWaveform();fail('波形采样',r);return}waveRows.push({time:Date.now(),values:r.data});if(waveRows.length>5000)waveRows.shift();drawWave();$('wave-state').textContent=`运行中 · ${waveRows.length} 点`}catch(e){stopWaveform();fail('波形采样',{error:String(e)});return}if(waveRunning)waveTimer=setTimeout(waveTick,Number($('wave-interval').value))}
function startWaveform(){try{if(!device)throw Error('请先连接设备');waveSignals=waveSelection();waveRows=[];waveRunning=true;clearTimeout(waveTimer);$('wave-state').textContent='正在采样…';drawWave();waveTick();log(`开始波形采样：${waveSignals.join(', ')}`)}catch(e){fail('波形',{error:String(e)})}}
function stopWaveform(){waveRunning=false;clearTimeout(waveTimer);$('wave-state').textContent=`已停止 · ${waveRows.length} 点`}
function clearWaveform(){waveRows=[];drawWave()}
async function saveWaveform(){if(!waveRows.length){fail('导出波形',{error:'当前没有采样数据'});return}let header=['unix_ms','time',...waveSignals],rows=[header,...waveRows.map(row=>[row.time,new Date(row.time).toISOString(),...waveSignals.map(s=>row.values[s])])];let r=await pywebview.api.save_capture('waveform',rows);if(!r.ok)fail('导出波形',r);else if(r.data)log(`波形 CSV 已保存：${r.data}`)}
setInterval(pollTerminal,120);$('terminal-input').addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();sendTerminal()}});
for(let id of ['port','slave'])$(id).addEventListener('change',()=>{device=null;pwmRows=[];$('pwm-body').innerHTML='<tr><td colspan="5" class="empty">连接信息已更改，请重新连接并读取</td></tr>';$('pwm-note').textContent='请连接设备后配置 PWM';working(false);status('连接信息已更改，请点击连接并读取')});working(false);window.addEventListener('pywebviewready',async()=>{await loadPorts();if($('port').value)await connectDevice()});
</script></body></html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="", help="preferred USB CDC port")
    args = parser.parse_args()
    webview.create_window(
        "ESP32-S3 USB Modbus IO 测试工具",
        html=HTML,
        js_api=Api(args.port),
        width=1180,
        height=800,
        min_size=(940, 650),
        background_color="#f3f6fa",
    )
    webview.start(debug=False)


if __name__ == "__main__":
    main()
