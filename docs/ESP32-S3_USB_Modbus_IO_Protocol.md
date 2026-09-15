# ESP32-S3 USB Modbus IO 通信协议

版本 1.0

本文档说明 ESP32-S3-WROOM-1 开发板通过原生 USB CDC 虚拟串口提供的 Modbus RTU IO 服务。主站可按通道读取数字输入与输出状态、控制数字输出、读取 GPIO1 到 GPIO18 的 ADC 原始值和校准毫伏值，并设置 GPIO 工作模式。固件默认从站地址为 1。

## 1 适用硬件

固件按图示双 USB-C ESP32-S3-WROOM-1 开发板设计。通信必须使用连接 GPIO19 和 GPIO20 的原生 USB 或 OTG 接口。GPIO19 是 USB D-，GPIO20 是 USB D+，因此不进入 IO 地址表。USB CDC 是字节流传输，电脑端设置的波特率、数据位、停止位和校验位不改变链路实际速率。

GPIO35、GPIO36、GPIO37 在数字地址表中保留稳定通道号，但默认不可访问。这三个引脚可能连接 N8R8 或 N16R8 模组的八线 PSRAM。只有确认具体模组未占用后，才能打开 `CONFIG_USB_MODBUS_ENABLE_GPIO35_37`。

## 2 电气要求

- GPIO 逻辑电平为 3.3 V，不得直接输入 5 V。
- ADC 输入限于 GPIO1 到 GPIO18。建议被测信号源阻抗较低，并保证输入始终处于芯片允许范围。
- GPIO 输出用于逻辑控制。继电器、线圈、电机、灯带和其他大电流负载必须通过晶体管、MOSFET、驱动芯片或隔离模块连接。
- GPIO0、GPIO3、GPIO45、GPIO46 是启动绑带引脚。外部电路不能在上电或复位采样期间改变所需启动电平。
- GPIO0 通常还连接 BOOT 按键，GPIO48 通常连接板载 RGB LED。使用这些通道会同时影响板载电路。
- GPIO39 到 GPIO42 具有 JTAG 复用功能，GPIO43 和 GPIO44 具有 UART0 复用功能。固件使用 USB CDC 并关闭控制台，运行后可将它们用作普通 GPIO。

## 3 RTU 帧格式

请求和响应均使用标准 Modbus RTU 应用数据单元。

| 字段 | 长度 | 说明 |
|---|---:|---|
| 从站地址 | 1 字节 | 默认 `0x01`，范围 1 到 247；地址 0 仅用于广播写 |
| 功能码 | 1 字节 | 见功能码表 |
| 数据 | 可变 | 多字节数值采用高字节在前 |
| CRC16 | 2 字节 | Modbus CRC16，多项式 `0xA001`，低字节先发送 |

USB 可能把一帧拆成多个数据块，也可能在一个数据块内携带多帧。固件根据功能码和长度字段重组请求。完整帧立即处理；不完整字节流超过 20 ms 未继续接收时丢弃。主站应在一次串口写操作中提交完整 RTU 帧，并等待响应后再发送下一条请求。

广播地址 0 只接受写单线圈、写单寄存器、写多线圈和写多寄存器。设备执行合法广播写，但不发送响应。广播读被忽略。

## 4 功能码

| 功能码 | 名称 | 用途 |
|---:|---|---|
| `0x01` | Read Coils | 读取数字输出命令状态 |
| `0x02` | Read Discrete Inputs | 读取 GPIO 实际逻辑电平 |
| `0x03` | Read Holding Registers | 读取通道模式、GPIO 映射和设备信息 |
| `0x04` | Read Input Registers | 读取 ADC 原始值或校准毫伏值 |
| `0x05` | Write Single Coil | 设置一个数字输出，写入时自动切换为输出模式 |
| `0x06` | Write Single Register | 设置一个通道的 GPIO 模式 |
| `0x0F` | Write Multiple Coils | 批量设置数字输出，写入时自动切换为输出模式 |
| `0x10` | Write Multiple Registers | 批量设置连续通道的 GPIO 模式 |

未实现的功能码返回异常 `0x01`。

## 5 数字通道映射

Modbus PDU 地址从 0 开始。文档中的传统线圈号和离散输入号从 1 开始显示。例如数字通道 0 的 PDU 地址是 `0x0000`，传统线圈号是 00001，传统离散输入号是 10001。

| 数字通道 | GPIO | 线圈号 | 离散输入号 | 默认状态 | 备注 |
|---:|---:|---:|---:|---|---|
| 0 | 0 | 00001 | 10001 | 浮空输入 | 启动绑带和 BOOT 按键 |
| 1 | 1 | 00002 | 10002 | 浮空输入 | ADC 模拟通道 0 |
| 2 | 2 | 00003 | 10003 | 浮空输入 | ADC 模拟通道 1 |
| 3 | 3 | 00004 | 10004 | 浮空输入 | ADC 模拟通道 2，启动绑带 |
| 4 | 4 | 00005 | 10005 | 浮空输入 | ADC 模拟通道 3 |
| 5 | 5 | 00006 | 10006 | 浮空输入 | ADC 模拟通道 4 |
| 6 | 6 | 00007 | 10007 | 浮空输入 | ADC 模拟通道 5 |
| 7 | 7 | 00008 | 10008 | 浮空输入 | ADC 模拟通道 6 |
| 8 | 8 | 00009 | 10009 | 浮空输入 | ADC 模拟通道 7 |
| 9 | 9 | 00010 | 10010 | 浮空输入 | ADC 模拟通道 8 |
| 10 | 10 | 00011 | 10011 | 浮空输入 | ADC 模拟通道 9 |
| 11 | 11 | 00012 | 10012 | 浮空输入 | ADC 模拟通道 10 |
| 12 | 12 | 00013 | 10013 | 浮空输入 | ADC 模拟通道 11 |
| 13 | 13 | 00014 | 10014 | 浮空输入 | ADC 模拟通道 12 |
| 14 | 14 | 00015 | 10015 | 浮空输入 | ADC 模拟通道 13 |
| 15 | 15 | 00016 | 10016 | 浮空输入 | ADC 模拟通道 14 |
| 16 | 16 | 00017 | 10017 | 浮空输入 | ADC 模拟通道 15 |
| 17 | 17 | 00018 | 10018 | 浮空输入 | ADC 模拟通道 16 |
| 18 | 18 | 00019 | 10019 | 浮空输入 | ADC 模拟通道 17 |
| 19 | 21 | 00020 | 10020 | 浮空输入 | 通用 IO |
| 20 | 38 | 00021 | 10021 | 浮空输入 | 通用 IO |
| 21 | 39 | 00022 | 10022 | 浮空输入 | JTAG 复用 |
| 22 | 40 | 00023 | 10023 | 浮空输入 | JTAG 复用 |
| 23 | 41 | 00024 | 10024 | 浮空输入 | JTAG 复用 |
| 24 | 42 | 00025 | 10025 | 浮空输入 | JTAG 复用 |
| 25 | 43 | 00026 | 10026 | 浮空输入 | 板上标记 TX，UART0 复用 |
| 26 | 44 | 00027 | 10027 | 浮空输入 | 板上标记 RX，UART0 复用 |
| 27 | 45 | 00028 | 10028 | 浮空输入 | 启动绑带 |
| 28 | 46 | 00029 | 10029 | 浮空输入 | 启动绑带 |
| 29 | 47 | 00030 | 10030 | 浮空输入 | 通用 IO |
| 30 | 48 | 00031 | 10031 | 浮空输入 | 常连接板载 RGB LED |
| 31 | 35 | 00032 | 10032 | 默认禁用 | 可能连接八线 PSRAM |
| 32 | 36 | 00033 | 10033 | 默认禁用 | 可能连接八线 PSRAM |
| 33 | 37 | 00034 | 10034 | 默认禁用 | 可能连接八线 PSRAM |

读取线圈返回最近写入的输出命令状态。读取离散输入返回引脚实际电平；输出模式使用输入输出双向配置，因此可读回物理电平。模拟模式关闭数字输入路径，直接读取相应离散输入会返回服务器设备故障异常。可先把模式改为浮空输入，再读取数字电平。

## 6 模拟输入寄存器

模拟通道 0 到 17 依次对应 GPIO1 到 GPIO18。ADC 使用 12 dB 衰减和默认位宽。

| PDU 地址范围 | 传统寄存器号 | 内容 | 数值 |
|---|---|---|---|
| `0x0000` 到 `0x0011` | 30001 到 30018 | ADC 原始值 | 典型范围 0 到 4095 |
| `0x0100` 到 `0x0111` | 30257 到 30274 | 校准电压 | 单位 mV；无校准时返回 `0xFFFF` |

读取模拟寄存器时，如果对应 GPIO 不是输出模式，固件会自动切换到模拟模式并采样。如果该 GPIO 正在输出，固件返回异常 `0x04`，避免在不明确的情况下改变输出。主站可先写保持寄存器把模式改为模拟输入。

校准毫伏值来自 ESP-IDF ADC 曲线拟合校准接口。芯片或构建配置不支持校准时，原始 ADC 寄存器仍然可用，毫伏寄存器返回 `0xFFFF`。ADC 结果受输入源阻抗、噪声、板级布局和芯片误差影响，不应直接作为计量级结果。

## 7 保持寄存器

### 7.1 GPIO 模式

PDU 地址 `0x0000` 到 `0x0021` 对应数字通道 0 到 33。可用 `0x03` 读取，用 `0x06` 或 `0x10` 写入。

| 模式值 | 含义 |
|---:|---|
| 0 | 浮空数字输入 |
| 1 | 上拉数字输入 |
| 2 | 下拉数字输入 |
| 3 | 数字输出；保留最近一次线圈命令值 |
| 4 | 模拟输入；仅数字通道 1 到 18 有效 |

写线圈会自动把相应通道切换到模式 3。把输出通道改回模式 0、1、2 或 4 会关闭数字输出驱动。不可用通道或不支持的模式组合返回异常。

### 7.2 GPIO 映射

PDU 地址 `0x0100` 到 `0x0121` 只读，对应数字通道 0 到 33，寄存器值是实际 GPIO 编号。默认禁用的 GPIO35 到 GPIO37 会返回非法数据地址异常，设备信息能力位可用于确认它们是否启用。

### 7.3 设备信息

| PDU 地址 | 传统寄存器号 | 内容 | 当前值 |
|---:|---:|---|---:|
| `0x0200` | 40513 | 协议版本 | `0x0100` 表示 1.0 |
| `0x0201` | 40514 | 固件版本 | `0x0100` 表示 1.0 |
| `0x0202` | 40515 | 数字通道总数 | 34 |
| `0x0203` | 40516 | 模拟通道总数 | 18 |
| `0x0204` | 40517 | 能力位 | bit0 有 ADC 校准；bit1 已启用 GPIO35 到 GPIO37 |

## 8 请求与响应格式

### 8.1 读位数据

功能码 `0x01` 和 `0x02` 的请求数据为起始地址和数量，各 2 字节。响应数据的第一个字节是后续字节数，位从每个字节的最低位开始排列。未使用的最高位填 0。

### 8.2 写单线圈

功能码 `0x05` 使用 `0xFF00` 表示高电平，使用 `0x0000` 表示低电平。其他数值返回异常 `0x03`。成功响应原样回显请求。

### 8.3 读寄存器

功能码 `0x03` 和 `0x04` 的请求数据为起始地址和寄存器数量。响应寄存器按高字节在前排列。

### 8.4 写单寄存器

功能码 `0x06` 只允许写 GPIO 模式地址。成功响应原样回显请求。

### 8.5 批量写

功能码 `0x0F` 按最低位优先顺序携带线圈值。功能码 `0x10` 的每个模式值占 2 字节。固件先校验整个地址范围和值，再执行写入。成功响应回显起始地址和写入数量。

## 9 异常响应

异常响应功能码等于请求功能码加 `0x80`。

| 异常码 | 名称 | 本固件中的含义 |
|---:|---|---|
| `0x01` | Illegal Function | 不支持该功能码 |
| `0x02` | Illegal Data Address | 地址越界、通道禁用或模式能力不匹配 |
| `0x03` | Illegal Data Value | 数量、字节数、线圈编码或模式值无效 |
| `0x04` | Server Device Failure | 当前模式冲突或底层 GPIO ADC 操作失败 |

CRC 错误、发给其他从站的请求以及不完整超时帧不会产生响应。

## 10 报文示例

以下十六进制字节均包含 CRC，CRC 低字节在前。

读取 5 个设备信息寄存器：

```text
请求  01 03 02 00 00 05 84 71
```

读取数字通道 0 到 7 的实际电平：

```text
请求  01 02 00 00 00 08 79 CC
响应  01 02 01 XX CRC_LO CRC_HI
```

把数字通道 1 设置为输出模式：

```text
请求  01 06 00 01 00 03 98 0B
响应  01 06 00 01 00 03 98 0B
```

把数字通道 1 对应的 GPIO1 输出高电平。写线圈也会自动进入输出模式，因此通常无需先发送上一条模式命令：

```text
请求  01 05 00 01 FF 00 DD FA
响应  01 05 00 01 FF 00 DD FA
```

读取模拟通道 0 对应 GPIO1 的 12 位原始值：

```text
请求  01 04 00 00 00 01 31 CA
响应  01 04 02 RAW_H RAW_L CRC_LO CRC_HI
```

读取模拟通道 0 对应 GPIO1 的校准毫伏值：

```text
请求  01 04 01 00 00 01 30 36
响应  01 04 02 MV_H MV_L CRC_LO CRC_HI
```

## 11 主机端测试

仓库内的 `tools/modbus_usb_client.py` 可直接执行常用命令。先安装 `pyserial`，再指定 CDC 串口。

```powershell
python -m pip install -r requirements.txt
python tools/modbus_usb_client.py --port COM8 info
python tools/modbus_usb_client.py --port COM8 read-map 0 31
python tools/modbus_usb_client.py --port COM8 read-inputs 0 8
python tools/modbus_usb_client.py --port COM8 write-output 1 on
python tools/modbus_usb_client.py --port COM8 read-analog 0 4
python tools/modbus_usb_client.py --port COM8 read-analog 0 4 --raw
python tools/modbus_usb_client.py --port COM8 set-mode 1 0
```

## 12 构建配置

```powershell
idf.py set-target esp32s3
idf.py menuconfig
idf.py build
idf.py -p COMx flash
```

菜单 `USB Modbus IO` 可设置从站地址、不完整帧超时和 GPIO35 到 GPIO37 的兼容开关。默认关闭控制台输出，UART0 日志不会污染 Modbus CDC 字节流。烧录后应把通信线连接到原生 USB 或 OTG 口；另一 USB-C 口如果经过 USB 转串口芯片，只用于 UART 烧录或调试，不承载本协议。

## 13 参考资料

- Espressif ESP-IDF USB Device Stack: https://docs.espressif.com/projects/esp-idf/en/v5.5/esp32s3/api-reference/peripherals/usb_device.html
- Espressif ESP-IDF ADC Oneshot Driver: https://docs.espressif.com/projects/esp-idf/en/v5.5/esp32s3/api-reference/peripherals/adc_oneshot.html
- Espressif ESP32-S3 Series Datasheet: https://documentation.espressif.com/esp32_s3_datasheet_en.pdf
- Modbus Application Protocol Specification V1.1b3: https://www.modbus.org/docs/Modbus_Application_Protocol_V1_1b3.pdf
- Modbus over Serial Line Specification and Implementation Guide V1.02: https://www.modbus.org/docs/Modbus_over_serial_line_V1_02.pdf
