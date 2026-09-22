# ESP32-S3 USB Modbus IO

本工程面向 ESP32-S3-WROOM-1 双 USB-C 开发板，使用 ESP-IDF 和 TinyUSB CDC-ACM 实现 Modbus RTU 串口 IO。从电脑上可读取数字输入、控制数字输出、读取 ADC 原始值和校准电压，并配置 PWM 频率和占空比。协议与固件版本为 1.1，保留 1.0 的数字 IO 和 ADC 地址。

PWM 支持 10 Hz～100 kHz、0%～100% 占空比；最多同时输出 8 路、使用 4 种不同频率。同频通道共享定时器，各通道占空比独立。所有参数只保存在内存中，重启后 GPIO 恢复浮空输入，PWM 全部关闭。

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

界面基于 Python 和 pywebview，支持端口选择、设备信息、数字 IO 状态、GPIO 模式设置、数字输出和 ADC 读取。PWM 页面可选择通道、输入频率（Hz）与占空比（%），应用并启动输出、停止输出或回读配置。连接旧版 1.0 固件时，原有功能仍可使用，PWM 控件按能力位禁用。

程序会优先自动选择 VID/PID 为 `303A:4001` 的 ESP32-S3 USB CDC 串口。仓库只保存源码与文档；固件二进制、EXE、`build/`、`dist/` 和其他构建产物不提交。

在 Windows 本地生成独立 EXE，输出为 `dist/ESP32-S3_Modbus_IO_Tool.exe`：

```powershell
python -m pip install pyinstaller
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

把数字通道 1 对应的 GPIO1 设置为 1 kHz、50% PWM，回读后停止：

```powershell
python tools/modbus_usb_client.py --port COM8 pwm-set 1 1000 50
python tools/modbus_usb_client.py --port COM8 pwm-read 1
python tools/modbus_usb_client.py --port COM8 pwm-stop 1
```

`pwm-set 1 1000 50 --disabled` 可保存本次运行期间的配置而不启动 PWM；若该通道正在输出 PWM，则停止并恢复浮空输入。

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
| `0x03` | Read Holding Registers | 读取 GPIO 模式、GPIO 映射、设备信息和 PWM 配置 |
| `0x04` | Read Input Registers | 读取 ADC 原始值或校准毫伏值 |
| `0x05` | Write Single Coil | 设置单路数字输出，并自动切换为输出模式 |
| `0x06` | Write Single Register | 设置单个 GPIO 通道模式 |
| `0x0F` | Write Multiple Coils | 批量设置数字输出 |
| `0x10` | Write Multiple Registers | 批量设置 GPIO 模式或写入单路完整 PWM 配置 |

### 地址映射

Modbus PDU 地址从 `0` 开始。

| 数据区 | PDU 地址 | 内容 |
|---|---|---|
| Coils | `0x0000`～`0x0021` | 数字通道 0～33 的输出命令状态 |
| Discrete Inputs | `0x0000`～`0x0021` | 数字通道 0～33 的 GPIO 实际电平 |
| Holding Registers | `0x0000`～`0x0021` | 数字通道 0～33 的工作模式 |
| Holding Registers | `0x0100`～`0x0121` | 数字通道 0～33 对应的 GPIO 编号 |
| Holding Registers | `0x0200`～`0x0206` | 协议版本、固件版本、通道数、能力位和 PWM 容量 |
| Holding Registers | `0x0300`～`0x0387` | 每个数字通道 4 个寄存器的 PWM 配置记录 |
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
| `5` | PWM 输出，使用保存的频率和占空比；初始为 1000 Hz、50% |

写线圈会停止该通道 PWM 并进入数字输出；把 PWM 通道切换为模式 0～4 也会停止 PWM。读取 ADC 时，固件会自动切换到模拟模式，但对数字输出或 PWM 通道返回异常 `0x04`，保持原有输出。没有 ADC 校准数据时，毫伏寄存器返回 `0xFFFF`。PWM 模式下读离散输入只得到采样瞬间电平，读线圈仍得到最近一次数字输出命令值，都不能用来判断占空比。

### 设备信息寄存器

| 地址 | 内容 |
|---:|---|
| `0x0200` | 协议版本，当前 `0x0101` 表示 1.1 |
| `0x0201` | 固件版本，当前 `0x0101` 表示 1.1 |
| `0x0202` | 数字通道总数，当前为 34 |
| `0x0203` | 模拟通道总数，当前为 18 |
| `0x0204` | 能力位：bit0 表示 ADC 校准可用，bit1 表示 GPIO35～37 已启用，bit2 表示支持 PWM |
| `0x0205` | 最大同时 PWM 输出通道数，当前为 8 |
| `0x0206` | 最大同时使用的 PWM 频率种类，当前为 4 |

前 5 个寄存器保持兼容。主站先读取这 5 个寄存器，确认 bit2 后再读取 PWM 扩展；旧版 1.0 固件没有 PWM 扩展寄存器。

### PWM 配置寄存器

数字通道 `N` 的记录起始地址为 `0x0300 + 4 × N`。GPIO35～37 对应的通道 31～33 仍受构建开关限制。

| 记录偏移 | 内容 | 范围 |
|---:|---|---|
| `+0` | 频率高 16 位 | 与下一寄存器拼成 32 位无符号 Hz 值 |
| `+1` | 频率低 16 位 | 10～100000 Hz，高字在前 |
| `+2` | 占空比 | 0～10000，每单位 0.01%；5000 表示 50% |
| `+3` | PWM 使能 | 0 停止，1 启动；读取表示当前是否处于 PWM 模式 |

通过功能码 `0x10` 一次写入对齐的完整 4 寄存器记录，同时应用频率、占空比和使能。`0x06` 写 PWM 配置、部分记录写入及跨记录写入返回 `0x03`。使能为 0 时仍保存参数；当前为 PWM 则停止并改为浮空输入，其他模式保持原样。包含模式 5 的 GPIO 模式批量写仅允许数量为 1，其余模式仍支持批量写。

PWM 使用 LEDC 和 40 MHz XTAL 时钟，按频率选择 8～14 位计数分辨率；0.01% 是协议输入步进，不保证实际波形达到该精度，实际频率也受硬件分频量化影响。回读返回请求参数，不是波形测量值。若请求需要的资源超出 8 路输出或 4 种频率上限，则返回 `0x06`，原有输出保持不变；可改用已有频率，或先停止一条不再使用的输出。0% 输出常低，100% 输出常高，两者仍占用 PWM 资源。

### 异常与广播

| 异常码 | 含义 |
|---:|---|
| `0x01` | 不支持的功能码 |
| `0x02` | 地址越界、通道禁用或模式不支持 |
| `0x03` | 数量、字节数或写入值无效 |
| `0x04` | 当前模式冲突或底层 GPIO/ADC/PWM 操作失败 |
| `0x06` | PWM 通道或定时器资源不足，设备忙 |

地址 `0` 可用于广播写操作，设备执行合法写请求但不返回响应。CRC 错误、发往其他从站的请求以及超时的不完整帧也不会产生响应。

### 报文示例

```text
# 读取 5 个设备信息寄存器
01 03 02 00 00 05 84 71

# 将数字通道 1 输出高电平
01 05 00 01 FF 00 DD FA

# 读取模拟通道 0 的 ADC 原始值
01 04 00 00 00 01 31 CA

# 通道 1 设置 1000 Hz、50% 并启动 PWM
01 10 03 04 00 04 08 00 00 03 E8 13 88 00 01 67 3C

# 读取通道 1 的 4 个 PWM 配置寄存器
01 03 03 04 00 04 05 8C
```

批量写、完整 GPIO 对照表、响应格式和更多示例请参阅上述完整协议文档。

## 自动测试

```powershell
python -m unittest discover -s tests -v
```

测试覆盖主机报文、参数校验、旧固件兼容，以及实际 C 协议处理和 PWM 资源分配代码。C 测试使用模拟 GPIO、ADC 和 LEDC 接口，需要本机 C 编译器（可用 `PWM_TEST_CC` 或 `CC` 指定）；未安装时会明确显示跳过。GitHub Actions 在 Ubuntu 上执行完整测试。硬件模拟测试不替代示波器测量和开发板联调。

## 工程结构

- `main/io_model.c`：GPIO 模式、数字 IO、ADC 单次采样与校准、LEDC PWM 资源管理。
- `main/modbus_server.c`：Modbus RTU 功能码、CRC 和数据模型。
- `main/usb_modbus.c`：TinyUSB CDC 收发、分帧、超时和流重同步。
- `tools/modbus_usb_client.py`：Windows/Linux/macOS 主机端测试程序。
- `tools/modbus_usb_gui.py`：数字 IO、ADC 和 PWM 图形上位机。
- `tests/`：协议和上位机测试。
- `scripts/build_protocol_docx.py`：生成 Word 协议文档；开发板图片位于 `docs/assets/`，也可通过 `--board-image` 指定。
