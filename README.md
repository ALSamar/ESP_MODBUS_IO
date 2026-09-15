# ESP32-S3 USB Modbus IO

本工程面向图示 ESP32-S3-WROOM-1 双 USB-C 开发板，使用 ESP-IDF 和 TinyUSB CDC-ACM 实现 Modbus RTU 从站。电脑将开发板识别为虚拟串口后，可以通过标准 Modbus 功能码读取数字输入、控制数字输出、读取 ADC 原始值和校准电压，并设置各通道模式。

## 硬件与安全约束

- 使用连接 ESP32-S3 GPIO19/GPIO20 的原生 USB/OTG 接口；它们分别是 USB D- 和 D+，不能同时作为 IO。
- GPIO1 到 GPIO18 支持 ADC。输入电压必须位于芯片允许范围内，绝不能把 5 V 直接接到 GPIO。
- GPIO35、GPIO36、GPIO37 在 N8R8/N16R8 等八线 PSRAM 模组上被内部存储器占用，默认禁用。只有确认模组接线兼容后，才能在 `menuconfig` 中打开对应选项。
- GPIO0、GPIO3、GPIO45、GPIO46 是启动绑带引脚。外部电路不得在复位期间强迫错误电平。
- GPIO48 通常连接板载 RGB LED，GPIO0 通常连接 BOOT 按键。
- GPIO 输出只适合逻辑信号。驱动继电器、电机、灯带或其他大电流负载时必须增加驱动器和保护电路。

## 构建与烧录

建议使用 ESP-IDF 5.4 或 5.5。首次构建时组件管理器会下载 `espressif/esp_tinyusb`。

```powershell
idf.py set-target esp32s3
idf.py build
idf.py -p COMx flash
```

工程默认关闭控制台输出，避免日志混入 Modbus 字节流，并释放 GPIO43/GPIO44。烧录完成后，将 USB 线接到原生 USB/OTG 口；如果没有出现新的 CDC 串口，请尝试开发板上的另一个 USB-C 接口并重新上电。

## 快速测试

安装主机端依赖：

```powershell
python -m pip install -r requirements.txt
```

Python 图形测试工具：

```powershell
python tools/modbus_usb_gui.py --port COM19
```

界面基于 Python 和 pywebview，支持端口选择、设备信息、数字 IO 状态、GPIO 模式设置、数字输出和 ADC 读取。

已打包的 Windows 独立程序位于 `dist/ESP32-S3_Modbus_IO_Tool.exe`，无需安装 Python，直接双击即可运行。程序会优先自动选择 VID/PID 为 `303A:4001` 的 ESP32-S3 USB CDC 串口。

重新生成 EXE：

```powershell
python -m PyInstaller --noconfirm --clean --onefile --windowed --name ESP32-S3_Modbus_IO_Tool --paths tools --collect-all webview --hidden-import webview.platforms.edgechromium tools/modbus_usb_gui.py
```

读取设备信息、GPIO 映射、数字输入和模拟输入：

```powershell
python tools/modbus_usb_client.py --port COM8 info
python tools/modbus_usb_client.py --port COM8 read-map 0 31
python tools/modbus_usb_client.py --port COM8 read-inputs 0 8
python tools/modbus_usb_client.py --port COM8 read-analog 0 4
```

把数字通道 1 对应的 GPIO1 拉高，再恢复为浮空输入：

```powershell
python tools/modbus_usb_client.py --port COM8 write-output 1 on
python tools/modbus_usb_client.py --port COM8 set-mode 1 0
```

完整寄存器映射、报文格式和异常码见：

- [`docs/ESP32-S3_USB_Modbus_IO_Protocol.md`](docs/ESP32-S3_USB_Modbus_IO_Protocol.md)
- [`docs/ESP32-S3_USB_Modbus_IO_Protocol.docx`](docs/ESP32-S3_USB_Modbus_IO_Protocol.docx)

## 通信协议速查

设备通过原生 USB CDC 虚拟串口提供 Modbus RTU 从站服务。默认从站地址为 `1`，CRC 使用标准 Modbus CRC16（多项式 `0xA001`，低字节先发送）。USB 是字节流链路，串口软件中设置的波特率、数据位、停止位和校验位不改变实际 USB 传输速率。

### 支持的功能码

| 功能码 | 名称 | 用途 |
|---:|---|---|
| `0x01` | Read Coils | 读取最近写入的数字输出状态 |
| `0x02` | Read Discrete Inputs | 读取 GPIO 实际逻辑电平 |
| `0x03` | Read Holding Registers | 读取 GPIO 模式、GPIO 映射和设备信息 |
| `0x04` | Read Input Registers | 读取 ADC 原始值或校准毫伏值 |
| `0x05` | Write Single Coil | 设置单路数字输出，并自动切换为输出模式 |
| `0x06` | Write Single Register | 设置单个 GPIO 通道模式 |
| `0x0F` | Write Multiple Coils | 批量设置数字输出 |
| `0x10` | Write Multiple Registers | 批量设置连续 GPIO 通道模式 |

### 地址映射

Modbus PDU 地址从 `0` 开始。

| 数据区 | PDU 地址 | 内容 |
|---|---|---|
| Coils | `0x0000`～`0x0021` | 数字通道 0～33 的输出命令状态 |
| Discrete Inputs | `0x0000`～`0x0021` | 数字通道 0～33 的 GPIO 实际电平 |
| Holding Registers | `0x0000`～`0x0021` | 数字通道 0～33 的工作模式 |
| Holding Registers | `0x0100`～`0x0121` | 数字通道 0～33 对应的 GPIO 编号 |
| Holding Registers | `0x0200`～`0x0204` | 协议版本、固件版本、通道数和能力位 |
| Input Registers | `0x0000`～`0x0011` | 模拟通道 0～17 的 ADC 原始值 |
| Input Registers | `0x0100`～`0x0111` | 模拟通道 0～17 的校准电压，单位 mV |

默认数字通道映射如下：

```text
通道 0～18  -> GPIO0～18
通道 19    -> GPIO21
通道 20～30 -> GPIO38～48
通道 31～33 -> GPIO35～37（默认禁用）
```

GPIO19 和 GPIO20 用作 USB D- 和 D+，不进入 IO 地址表。模拟通道 0～17 分别对应 GPIO1～18。

### GPIO 模式值

| 值 | 模式 |
|---:|---|
| `0` | 浮空数字输入 |
| `1` | 上拉数字输入 |
| `2` | 下拉数字输入 |
| `3` | 数字输出 |
| `4` | 模拟输入，仅 GPIO1～18 支持 |

读取 ADC 时，固件会把对应 GPIO 自动切换到模拟模式；如果该通道正在输出，则返回服务器设备故障异常，避免意外改变输出状态。没有 ADC 校准数据时，毫伏寄存器返回 `0xFFFF`。

### 设备信息寄存器

| 地址 | 内容 |
|---:|---|
| `0x0200` | 协议版本，当前 `0x0100` 表示 1.0 |
| `0x0201` | 固件版本，当前 `0x0100` 表示 1.0 |
| `0x0202` | 数字通道总数，当前为 34 |
| `0x0203` | 模拟通道总数，当前为 18 |
| `0x0204` | 能力位：bit0 表示 ADC 校准可用，bit1 表示 GPIO35～37 已启用 |

### 异常与广播

| 异常码 | 含义 |
|---:|---|
| `0x01` | 不支持的功能码 |
| `0x02` | 地址越界、通道禁用或模式不支持 |
| `0x03` | 数量、字节数或写入值无效 |
| `0x04` | 当前模式冲突或底层 GPIO/ADC 操作失败 |

地址 `0` 可用于广播写操作，设备执行合法写请求但不返回响应。CRC 错误、发往其他从站的请求以及超时的不完整帧也不会产生响应。

### 报文示例

```text
# 读取 5 个设备信息寄存器
01 03 02 00 00 05 84 71

# 将数字通道 1 输出高电平
01 05 00 01 FF 00 DD FA

# 读取模拟通道 0 的 ADC 原始值
01 04 00 00 00 01 31 CA
```

批量写、完整 GPIO 对照表、响应格式和更多示例请参阅上述完整协议文档。

## 工程结构

- `main/io_model.c`：GPIO 模式、数字 IO、ADC 单次采样与校准。
- `main/modbus_server.c`：Modbus RTU 功能码、CRC 和数据模型。
- `main/usb_modbus.c`：TinyUSB CDC 收发、分帧、超时和流重同步。
- `tools/modbus_usb_client.py`：Windows/Linux/macOS 主机端测试程序。
- `tests/test_protocol.py`：CRC、位打包和寄存器字节序测试。
