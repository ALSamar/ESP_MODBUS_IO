#!/usr/bin/env python3
"""Python desktop GUI for the ESP32-S3 USB Modbus IO firmware."""

from __future__ import annotations

import argparse
import re
import threading
from typing import Any, Callable

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
)


MODE_NAMES = ("浮空输入", "上拉输入", "下拉输入", "数字输出", "模拟输入", "PWM 输出")


class Api:
    """Local Python API exposed to the embedded webview."""

    def __init__(self, preferred_port: str = "") -> None:
        self.preferred_port = preferred_port
        self._lock = threading.Lock()

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
        return Client(port, int(slave), timeout=0.7)

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
                while start < count and modes[start] in (4, PWM_MODE):
                    start += 1
                if start >= count:
                    break
                end = start
                while end < count and modes[end] not in (4, PWM_MODE):
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
                if modes[start] in (3, PWM_MODE):
                    start += 1
                    continue
                end = start + 1
                while end < count and modes[end] not in (3, PWM_MODE):
                    end += 1
                raw[start:end] = client.read_registers(FUNCTION_READ_INPUT_REGISTERS, start, end - start)
                millivolts[start:end] = client.read_registers(FUNCTION_READ_INPUT_REGISTERS, 0x0100 + start, end - start)
                start = end
            return [
                {
                    "channel": index,
                    "gpio": index + 1,
                    "raw": raw[index],
                    "state": "PWM 输出占用" if modes[index] == PWM_MODE else "数字输出占用" if modes[index] == 3 else "模拟输入",
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
</style></head><body>
<header><div><h1>ESP32-S3 · MODBUS IO</h1><p>Python USB CDC 设备调试与通道控制</p></div><div class="badge">Modbus RTU · USB CDC</div></header>
<section class="connect"><span class="small">串口</span><select id="port"></select><button onclick="loadPorts()">刷新端口</button><span class="small" style="margin-left:10px">从站</span><input id="slave" type="number" min="1" max="247" value="1"><button class="primary" onclick="connectDevice()">连接并读取</button><span id="status">尚未连接</span></section>
<nav><button class="active" data-page="info" onclick="showPage(this)">设备信息</button><button data-page="digital" onclick="showPage(this)">数字 IO</button><button data-page="analog" onclick="showPage(this)">模拟输入</button><button data-page="pwm" onclick="showPage(this)">PWM 输出</button><button data-page="logs" onclick="showPage(this)">运行日志</button></nav>
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
<section id="logs" class="page"><textarea id="log" readonly></textarea></section></main>
<script>
const $=id=>document.getElementById(id);let busy=0,device=null,pwmRows=[];const stamp=()=>new Date().toLocaleTimeString('zh-CN',{hour12:false});
function log(s){$('log').value+=`[${stamp()}] ${s}\n`;$('log').scrollTop=$('log').scrollHeight}function status(s,k=''){$('status').textContent=s;$('status').className=k}
function working(on,s='正在通信…'){busy=Math.max(0,busy+(on?1:-1));document.querySelectorAll('button').forEach(b=>b.disabled=busy>0||(b.hasAttribute('data-pwm')&&!device?.pwm));document.querySelectorAll('select,input').forEach(x=>x.disabled=busy>0);if(on)status(s,'busy')}
function conn(){return{port:$('port').value,slave:Number($('slave').value)}}function fail(name,r){let s=r?.error||'未知错误';status(`失败：${s}`,'bad');log(`${name}失败：${s}`)}
function showPage(b){document.querySelectorAll('nav button').forEach(x=>x.classList.remove('active'));b.classList.add('active');document.querySelectorAll('.page').forEach(x=>x.classList.remove('active'));$(b.dataset.page).classList.add('active')}
function level(v,label='—'){return v===null?`<span class="level na">${label}</span>`:v?'<span class="level on">高</span>':'<span class="level off">低</span>'}
function useInfo(d){device=d;for(let id of ['channel','pwm-channel']){let box=$(id),old=box.value;box.innerHTML='';for(let i=0;i<d.usable_digital;i++){let o=document.createElement('option');o.value=i;o.textContent=i;box.appendChild(o)}if([...box.options].some(o=>o.value===old))box.value=old}let mode=$('mode'),extra=mode.querySelector('[value="5"]');if(d.pwm&&!extra){let o=document.createElement('option');o.value='5';o.textContent='PWM（已存参数）';mode.appendChild(o)}else if(!d.pwm&&extra)extra.remove();$('pwm-note').textContent=d.pwm?`10～100000 Hz；占空比 0～100%，步进 0.01%。最多 ${d.pwm_channels} 路同时输出、${d.pwm_frequencies} 种频率；同频率通道共享定时器。表中显示设定值，实际波形受硬件分辨率量化。参数断电复位，停止后恢复浮空输入。`:'当前固件不支持 PWM；数字 IO 和 ADC 可继续使用。更新到协议 1.1 后可配置 PWM。'}
async function loadPorts(){working(true,'正在扫描串口…');try{let old=$('port').value,r=await pywebview.api.get_ports(),box=$('port');box.innerHTML='';r.ports.forEach(p=>{let o=document.createElement('option');o.value=p.device;o.textContent=`${p.device} · ${p.description}`;box.appendChild(o)});box.value=[...box.options].some(o=>o.value===old)?old:r.recommended;log(`发现串口：${r.ports.map(p=>p.device).join(', ')||'无'}`);status(r.ports.length?'请选择端口并连接':'未发现串口',r.ports.length?'':'bad')}catch(e){fail('扫描',{error:String(e)})}finally{working(false)}}
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
