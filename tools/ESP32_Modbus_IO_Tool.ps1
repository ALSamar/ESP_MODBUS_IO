param(
    [string]$Port = ""
)

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

[System.Windows.Forms.Application]::EnableVisualStyles()
[System.Windows.Forms.Application]::SetCompatibleTextRenderingDefault($false)

$script:SlaveAddress = 1
$script:TimeoutMs = 700
$script:ModeNames = @("浮空输入", "上拉输入", "下拉输入", "数字输出", "模拟输入")
$script:DigitalGpios = @(0..18) + @(21, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48)
$script:AnalogGpios = @(1..18)

function Get-ModbusCrc {
    param([byte[]]$Data)
    [uint16]$crc = 0xFFFF
    foreach ($value in $Data) {
        $crc = $crc -bxor $value
        for ($bit = 0; $bit -lt 8; $bit++) {
            if (($crc -band 1) -ne 0) {
                $crc = [uint16](($crc -shr 1) -bxor 0xA001)
            } else {
                $crc = [uint16]($crc -shr 1)
            }
        }
    }
    return $crc
}

function New-ModbusRequest {
    param(
        [byte]$Function,
        [uint16]$Address,
        [uint16]$Value
    )
    [byte[]]$payload = @(
        [byte]$script:SlaveAddress,
        $Function,
        [byte]($Address -shr 8),
        [byte]($Address -band 0xFF),
        [byte]($Value -shr 8),
        [byte]($Value -band 0xFF)
    )
    [uint16]$crc = Get-ModbusCrc $payload
    [byte[]]$frame = $payload + @([byte]($crc -band 0xFF), [byte]($crc -shr 8))
    return ,$frame
}

function Invoke-Modbus {
    param([byte[]]$Request)

    $portName = [string]$script:PortBox.SelectedItem
    if ([string]::IsNullOrWhiteSpace($portName)) {
        throw "请选择 USB CDC 串口"
    }

    $serial = New-Object System.IO.Ports.SerialPort $portName, 115200, None, 8, One
    $serial.ReadTimeout = $script:TimeoutMs
    $serial.WriteTimeout = $script:TimeoutMs
    $serial.DtrEnable = $false
    $serial.RtsEnable = $false
    try {
        $serial.Open()
        $serial.DiscardInBuffer()
        $serial.Write($Request, 0, $Request.Length)

        $response = New-Object 'System.Collections.Generic.List[byte]'
        $timer = [System.Diagnostics.Stopwatch]::StartNew()
        $expected = -1
        while ($timer.ElapsedMilliseconds -lt $script:TimeoutMs) {
            $available = $serial.BytesToRead
            if ($available -gt 0) {
                [byte[]]$buffer = New-Object byte[] $available
                $read = $serial.Read($buffer, 0, $buffer.Length)
                for ($index = 0; $index -lt $read; $index++) {
                    $response.Add($buffer[$index])
                }
                if (($response.Count -ge 2) -and (($response[1] -band 0x80) -ne 0)) {
                    $expected = 5
                } elseif ($response.Count -ge 3) {
                    if ($response[1] -in @(1, 2, 3, 4)) {
                        $expected = 5 + $response[2]
                    } else {
                        $expected = 8
                    }
                }
                if (($expected -gt 0) -and ($response.Count -ge $expected)) { break }
            } else {
                Start-Sleep -Milliseconds 2
            }
        }
        [byte[]]$bytes = $response.ToArray()
    } finally {
        if ($serial.IsOpen) { $serial.Close() }
        $serial.Dispose()
    }

    if (-not $bytes -or $bytes.Length -eq 0) {
        throw "设备无响应，请确认选择的是 Modbus CDC 端口"
    }
    if (($expected -lt 0) -or ($bytes.Length -ne $expected)) {
        throw "响应长度异常：$([BitConverter]::ToString($bytes))"
    }
    [uint16]$receivedCrc = [uint16]($bytes[$bytes.Length - 2] -bor ($bytes[$bytes.Length - 1] -shl 8))
    [byte[]]$body = $bytes[0..($bytes.Length - 3)]
    if ((Get-ModbusCrc $body) -ne $receivedCrc) { throw "响应 CRC 校验失败" }
    if ($bytes[0] -ne $script:SlaveAddress) { throw "从站地址不匹配" }
    if (($bytes[1] -band 0x80) -ne 0) {
        throw "Modbus 异常 0x$('{0:X2}' -f $bytes[2])"
    }
    return ,$bytes
}

function Read-ModbusRegisters {
    param([byte]$Function, [uint16]$Start, [uint16]$Count)
    [byte[]]$response = Invoke-Modbus (New-ModbusRequest $Function $Start $Count)
    $values = New-Object 'System.Collections.Generic.List[uint16]'
    for ($offset = 0; $offset -lt $response[2]; $offset += 2) {
        $values.Add([uint16](($response[3 + $offset] -shl 8) -bor $response[4 + $offset]))
    }
    return ,$values.ToArray()
}

function Read-ModbusBits {
    param([byte]$Function, [uint16]$Start, [uint16]$Count)
    [byte[]]$response = Invoke-Modbus (New-ModbusRequest $Function $Start $Count)
    $values = New-Object 'System.Collections.Generic.List[bool]'
    for ($index = 0; $index -lt $Count; $index++) {
        $values.Add((($response[3 + [math]::Floor($index / 8)] -shr ($index % 8)) -band 1) -ne 0)
    }
    return ,$values.ToArray()
}

function Write-ModbusCoil {
    param([uint16]$Channel, [bool]$State)
    [uint16]$value = if ($State) { 0xFF00 } else { 0x0000 }
    [byte[]]$request = New-ModbusRequest 5 $Channel $value
    [byte[]]$response = Invoke-Modbus $request
    if ([BitConverter]::ToString($response) -ne [BitConverter]::ToString($request)) {
        throw "写线圈回显不匹配"
    }
}

function Write-ModbusRegister {
    param([uint16]$Address, [uint16]$Value)
    [byte[]]$request = New-ModbusRequest 6 $Address $Value
    [byte[]]$response = Invoke-Modbus $request
    if ([BitConverter]::ToString($response) -ne [BitConverter]::ToString($request)) {
        throw "写寄存器回显不匹配"
    }
}

function Write-UiLog {
    param([string]$Message)
    $timestamp = Get-Date -Format "HH:mm:ss"
    $script:LogBox.AppendText("[$timestamp] $Message`r`n")
    $script:LogBox.SelectionStart = $script:LogBox.TextLength
    $script:LogBox.ScrollToCaret()
}

function Invoke-UiAction {
    param([string]$Name, [scriptblock]$Action)
    $script:StatusLabel.Text = "正在执行：$Name"
    $script:StatusLabel.ForeColor = [System.Drawing.Color]::FromArgb(190, 120, 20)
    $script:Form.UseWaitCursor = $true
    [System.Windows.Forms.Application]::DoEvents()
    try {
        & $Action
        $script:StatusLabel.Text = "已连接 · $([string]$script:PortBox.SelectedItem) · 从站 $script:SlaveAddress"
        $script:StatusLabel.ForeColor = [System.Drawing.Color]::FromArgb(26, 125, 80)
        Write-UiLog "$Name：成功"
    } catch {
        $script:StatusLabel.Text = "操作失败：$($_.Exception.Message)"
        $script:StatusLabel.ForeColor = [System.Drawing.Color]::FromArgb(190, 45, 45)
        Write-UiLog "$Name：失败 - $($_.Exception.Message)"
        [System.Windows.Forms.MessageBox]::Show(
            $script:Form,
            $_.Exception.Message,
            "ESP32-S3 Modbus IO",
            [System.Windows.Forms.MessageBoxButtons]::OK,
            [System.Windows.Forms.MessageBoxIcon]::Warning
        ) | Out-Null
    } finally {
        $script:Form.UseWaitCursor = $false
    }
}

function Refresh-PortList {
    $previous = [string]$script:PortBox.SelectedItem
    $ports = [System.IO.Ports.SerialPort]::GetPortNames() |
        Sort-Object { [int]($_ -replace '[^0-9]', '') }
    $script:PortBox.Items.Clear()
    foreach ($name in $ports) { [void]$script:PortBox.Items.Add($name) }

    $wanted = if (-not [string]::IsNullOrWhiteSpace($Port)) { $Port } elseif ($ports -contains $previous) { $previous } else { $ports | Select-Object -Last 1 }
    if ($ports -contains $wanted) { $script:PortBox.SelectedItem = $wanted }
    elseif ($script:PortBox.Items.Count -gt 0) { $script:PortBox.SelectedIndex = $script:PortBox.Items.Count - 1 }
    Write-UiLog "发现串口：$(if ($ports) { $ports -join ', ' } else { '无' })"
}

function Read-DeviceInfo {
    $script:SlaveAddress = [int]$script:SlaveBox.Value
    [uint16[]]$info = Read-ModbusRegisters 3 0x0200 5
    $script:ProtocolValue.Text = "$($info[0] -shr 8).$($info[0] -band 0xFF)"
    $script:FirmwareValue.Text = "$($info[1] -shr 8).$($info[1] -band 0xFF)"
    $script:DigitalValue.Text = [string]$info[2]
    $script:AnalogValue.Text = [string]$info[3]
    $script:CalibrationValue.Text = if (($info[4] -band 1) -ne 0) { "可用" } else { "不可用" }
    $script:ReservedValue.Text = if (($info[4] -band 2) -ne 0) { "已启用" } else { "安全禁用" }
}

function Refresh-DigitalTable {
    $script:SlaveAddress = [int]$script:SlaveBox.Value
    [uint16[]]$modes = Read-ModbusRegisters 3 0 31
    [uint16[]]$mapping = Read-ModbusRegisters 3 0x0100 31
    [bool[]]$outputs = Read-ModbusBits 1 0 31
    $script:DigitalGrid.Rows.Clear()

    for ($channel = 0; $channel -lt 31; $channel++) {
        $inputText = "—"
        if ($modes[$channel] -ne 4) {
            [bool[]]$inputValue = Read-ModbusBits 2 $channel 1
            $inputText = if ($inputValue[0]) { "高" } else { "低" }
        }
        $modeText = if ($modes[$channel] -lt $script:ModeNames.Count) { $script:ModeNames[$modes[$channel]] } else { "未知" }
        $outputText = if ($outputs[$channel]) { "高" } else { "低" }
        [void]$script:DigitalGrid.Rows.Add($channel, "GPIO$($mapping[$channel])", $modeText, $inputText, $outputText)
    }
}

function Refresh-AnalogTable {
    $script:SlaveAddress = [int]$script:SlaveBox.Value
    [uint16[]]$raw = Read-ModbusRegisters 4 0x0000 18
    [uint16[]]$millivolts = Read-ModbusRegisters 4 0x0100 18
    $script:AnalogGrid.Rows.Clear()
    for ($channel = 0; $channel -lt 18; $channel++) {
        $mvText = if ($millivolts[$channel] -eq 0xFFFF) { "不可用" } else { "$($millivolts[$channel]) mV" }
        [void]$script:AnalogGrid.Rows.Add($channel, "GPIO$($script:AnalogGpios[$channel])", $raw[$channel], $mvText)
    }
}

function New-ValueCard {
    param([System.Windows.Forms.Control]$Parent, [string]$Title, [int]$X, [int]$Y)
    $panel = New-Object System.Windows.Forms.Panel
    $panel.Location = New-Object System.Drawing.Point($X, $Y)
    $panel.Size = New-Object System.Drawing.Size(250, 100)
    $panel.BackColor = [System.Drawing.Color]::White
    $panel.BorderStyle = [System.Windows.Forms.BorderStyle]::FixedSingle
    $Parent.Controls.Add($panel)

    $caption = New-Object System.Windows.Forms.Label
    $caption.Text = $Title
    $caption.Location = New-Object System.Drawing.Point(16, 14)
    $caption.AutoSize = $true
    $caption.ForeColor = [System.Drawing.Color]::FromArgb(95, 105, 120)
    $caption.Font = New-Object System.Drawing.Font("Microsoft YaHei UI", 9)
    $panel.Controls.Add($caption)

    $value = New-Object System.Windows.Forms.Label
    $value.Text = "—"
    $value.Location = New-Object System.Drawing.Point(16, 43)
    $value.AutoSize = $true
    $value.ForeColor = [System.Drawing.Color]::FromArgb(25, 45, 72)
    $value.Font = New-Object System.Drawing.Font("Microsoft YaHei UI", 18, [System.Drawing.FontStyle]::Bold)
    $panel.Controls.Add($value)
    return $value
}

$script:Form = New-Object System.Windows.Forms.Form
$script:Form.Text = "ESP32-S3 USB Modbus IO 测试工具"
$script:Form.StartPosition = [System.Windows.Forms.FormStartPosition]::CenterScreen
$script:Form.Size = New-Object System.Drawing.Size(1100, 760)
$script:Form.MinimumSize = New-Object System.Drawing.Size(920, 650)
$script:Form.BackColor = [System.Drawing.Color]::FromArgb(244, 247, 251)
$script:Form.Font = New-Object System.Drawing.Font("Microsoft YaHei UI", 9)
$script:Form.AutoScaleMode = [System.Windows.Forms.AutoScaleMode]::Dpi

$header = New-Object System.Windows.Forms.Panel
$header.Dock = [System.Windows.Forms.DockStyle]::Top
$header.Height = 76
$header.BackColor = [System.Drawing.Color]::FromArgb(28, 54, 88)
$script:Form.Controls.Add($header)

$title = New-Object System.Windows.Forms.Label
$title.Text = "ESP32-S3 · MODBUS IO"
$title.Location = New-Object System.Drawing.Point(22, 14)
$title.AutoSize = $true
$title.ForeColor = [System.Drawing.Color]::White
$title.Font = New-Object System.Drawing.Font("Microsoft YaHei UI", 18, [System.Drawing.FontStyle]::Bold)
$header.Controls.Add($title)

$subtitle = New-Object System.Windows.Forms.Label
$subtitle.Text = "USB CDC 设备调试与通道控制"
$subtitle.Location = New-Object System.Drawing.Point(25, 47)
$subtitle.AutoSize = $true
$subtitle.ForeColor = [System.Drawing.Color]::FromArgb(190, 207, 226)
$header.Controls.Add($subtitle)

$connection = New-Object System.Windows.Forms.Panel
$connection.Dock = [System.Windows.Forms.DockStyle]::Top
$connection.Height = 68
$connection.Padding = New-Object System.Windows.Forms.Padding(18, 14, 18, 10)
$connection.BackColor = [System.Drawing.Color]::White
$script:Form.Controls.Add($connection)

$portLabel = New-Object System.Windows.Forms.Label
$portLabel.Text = "串口"
$portLabel.Location = New-Object System.Drawing.Point(20, 23)
$portLabel.AutoSize = $true
$connection.Controls.Add($portLabel)

$script:PortBox = New-Object System.Windows.Forms.ComboBox
$script:PortBox.Location = New-Object System.Drawing.Point(64, 18)
$script:PortBox.Size = New-Object System.Drawing.Size(120, 28)
$script:PortBox.DropDownStyle = [System.Windows.Forms.ComboBoxStyle]::DropDownList
$connection.Controls.Add($script:PortBox)

$refreshPortsButton = New-Object System.Windows.Forms.Button
$refreshPortsButton.Text = "刷新端口"
$refreshPortsButton.Location = New-Object System.Drawing.Point(194, 17)
$refreshPortsButton.Size = New-Object System.Drawing.Size(90, 30)
$connection.Controls.Add($refreshPortsButton)

$slaveLabel = New-Object System.Windows.Forms.Label
$slaveLabel.Text = "从站"
$slaveLabel.Location = New-Object System.Drawing.Point(310, 23)
$slaveLabel.AutoSize = $true
$connection.Controls.Add($slaveLabel)

$script:SlaveBox = New-Object System.Windows.Forms.NumericUpDown
$script:SlaveBox.Location = New-Object System.Drawing.Point(354, 18)
$script:SlaveBox.Size = New-Object System.Drawing.Size(70, 28)
$script:SlaveBox.Minimum = 1
$script:SlaveBox.Maximum = 247
$script:SlaveBox.Value = 1
$connection.Controls.Add($script:SlaveBox)

$connectButton = New-Object System.Windows.Forms.Button
$connectButton.Text = "连接并读取"
$connectButton.Location = New-Object System.Drawing.Point(444, 16)
$connectButton.Size = New-Object System.Drawing.Size(120, 32)
$connectButton.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
$connectButton.BackColor = [System.Drawing.Color]::FromArgb(38, 116, 184)
$connectButton.ForeColor = [System.Drawing.Color]::White
$connectButton.FlatAppearance.BorderSize = 0
$connection.Controls.Add($connectButton)

$script:StatusLabel = New-Object System.Windows.Forms.Label
$script:StatusLabel.Text = "尚未连接"
$script:StatusLabel.Location = New-Object System.Drawing.Point(590, 23)
$script:StatusLabel.AutoSize = $true
$script:StatusLabel.ForeColor = [System.Drawing.Color]::FromArgb(105, 115, 130)
$connection.Controls.Add($script:StatusLabel)

$tabs = New-Object System.Windows.Forms.TabControl
$tabs.Dock = [System.Windows.Forms.DockStyle]::Fill
$tabs.Padding = New-Object System.Drawing.Point(18, 8)
$script:Form.Controls.Add($tabs)
$tabs.BringToFront()

$infoTab = New-Object System.Windows.Forms.TabPage
$infoTab.Text = "设备信息"
$infoTab.BackColor = [System.Drawing.Color]::FromArgb(244, 247, 251)
$tabs.TabPages.Add($infoTab)

$script:ProtocolValue = New-ValueCard $infoTab "协议版本" 28 30
$script:FirmwareValue = New-ValueCard $infoTab "固件版本" 298 30
$script:DigitalValue = New-ValueCard $infoTab "数字通道" 568 30
$script:AnalogValue = New-ValueCard $infoTab "模拟通道" 838 30
$script:CalibrationValue = New-ValueCard $infoTab "ADC 校准" 28 150
$script:ReservedValue = New-ValueCard $infoTab "GPIO35–37" 298 150

$hintPanel = New-Object System.Windows.Forms.Panel
$hintPanel.Location = New-Object System.Drawing.Point(568, 150)
$hintPanel.Size = New-Object System.Drawing.Size(520, 100)
$hintPanel.BackColor = [System.Drawing.Color]::FromArgb(231, 241, 251)
$infoTab.Controls.Add($hintPanel)
$hint = New-Object System.Windows.Forms.Label
$hint.Text = "提示：GPIO19/20 已由原生 USB 占用。未接信号的输入引脚会出现随机电平或悬空 ADC 数值。"
$hint.Location = New-Object System.Drawing.Point(18, 18)
$hint.Size = New-Object System.Drawing.Size(480, 60)
$hint.ForeColor = [System.Drawing.Color]::FromArgb(45, 85, 120)
$hintPanel.Controls.Add($hint)

$digitalTab = New-Object System.Windows.Forms.TabPage
$digitalTab.Text = "数字 IO"
$digitalTab.BackColor = [System.Drawing.Color]::White
$tabs.TabPages.Add($digitalTab)

$digitalToolbar = New-Object System.Windows.Forms.Panel
$digitalToolbar.Dock = [System.Windows.Forms.DockStyle]::Top
$digitalToolbar.Height = 60
$digitalToolbar.BackColor = [System.Drawing.Color]::FromArgb(245, 248, 252)
$digitalTab.Controls.Add($digitalToolbar)

$refreshDigitalButton = New-Object System.Windows.Forms.Button
$refreshDigitalButton.Text = "刷新数字 IO"
$refreshDigitalButton.Location = New-Object System.Drawing.Point(16, 14)
$refreshDigitalButton.Size = New-Object System.Drawing.Size(116, 32)
$digitalToolbar.Controls.Add($refreshDigitalButton)

$channelLabel = New-Object System.Windows.Forms.Label
$channelLabel.Text = "通道"
$channelLabel.Location = New-Object System.Drawing.Point(164, 22)
$channelLabel.AutoSize = $true
$digitalToolbar.Controls.Add($channelLabel)

$script:ChannelBox = New-Object System.Windows.Forms.NumericUpDown
$script:ChannelBox.Location = New-Object System.Drawing.Point(208, 17)
$script:ChannelBox.Size = New-Object System.Drawing.Size(66, 28)
$script:ChannelBox.Minimum = 0
$script:ChannelBox.Maximum = 30
$digitalToolbar.Controls.Add($script:ChannelBox)

$script:ModeBox = New-Object System.Windows.Forms.ComboBox
$script:ModeBox.Location = New-Object System.Drawing.Point(294, 17)
$script:ModeBox.Size = New-Object System.Drawing.Size(124, 28)
$script:ModeBox.DropDownStyle = [System.Windows.Forms.ComboBoxStyle]::DropDownList
[void]$script:ModeBox.Items.AddRange($script:ModeNames)
$script:ModeBox.SelectedIndex = 0
$digitalToolbar.Controls.Add($script:ModeBox)

$setModeButton = New-Object System.Windows.Forms.Button
$setModeButton.Text = "设置模式"
$setModeButton.Location = New-Object System.Drawing.Point(428, 15)
$setModeButton.Size = New-Object System.Drawing.Size(92, 32)
$digitalToolbar.Controls.Add($setModeButton)

$highButton = New-Object System.Windows.Forms.Button
$highButton.Text = "输出高"
$highButton.Location = New-Object System.Drawing.Point(548, 15)
$highButton.Size = New-Object System.Drawing.Size(90, 32)
$highButton.BackColor = [System.Drawing.Color]::FromArgb(223, 247, 233)
$digitalToolbar.Controls.Add($highButton)

$lowButton = New-Object System.Windows.Forms.Button
$lowButton.Text = "输出低"
$lowButton.Location = New-Object System.Drawing.Point(648, 15)
$lowButton.Size = New-Object System.Drawing.Size(90, 32)
$lowButton.BackColor = [System.Drawing.Color]::FromArgb(252, 232, 232)
$digitalToolbar.Controls.Add($lowButton)

$script:DigitalGrid = New-Object System.Windows.Forms.DataGridView
$script:DigitalGrid.Dock = [System.Windows.Forms.DockStyle]::Fill
$script:DigitalGrid.AllowUserToAddRows = $false
$script:DigitalGrid.AllowUserToDeleteRows = $false
$script:DigitalGrid.ReadOnly = $true
$script:DigitalGrid.RowHeadersVisible = $false
$script:DigitalGrid.AutoSizeColumnsMode = [System.Windows.Forms.DataGridViewAutoSizeColumnsMode]::Fill
$script:DigitalGrid.SelectionMode = [System.Windows.Forms.DataGridViewSelectionMode]::FullRowSelect
$script:DigitalGrid.BackgroundColor = [System.Drawing.Color]::White
[void]$script:DigitalGrid.Columns.Add("channel", "通道")
[void]$script:DigitalGrid.Columns.Add("gpio", "GPIO")
[void]$script:DigitalGrid.Columns.Add("mode", "模式")
[void]$script:DigitalGrid.Columns.Add("input", "输入电平")
[void]$script:DigitalGrid.Columns.Add("output", "输出状态")
$digitalTab.Controls.Add($script:DigitalGrid)
$script:DigitalGrid.BringToFront()

$analogTab = New-Object System.Windows.Forms.TabPage
$analogTab.Text = "模拟输入"
$analogTab.BackColor = [System.Drawing.Color]::White
$tabs.TabPages.Add($analogTab)

$analogToolbar = New-Object System.Windows.Forms.Panel
$analogToolbar.Dock = [System.Windows.Forms.DockStyle]::Top
$analogToolbar.Height = 60
$analogToolbar.BackColor = [System.Drawing.Color]::FromArgb(245, 248, 252)
$analogTab.Controls.Add($analogToolbar)

$refreshAnalogButton = New-Object System.Windows.Forms.Button
$refreshAnalogButton.Text = "读取全部 ADC"
$refreshAnalogButton.Location = New-Object System.Drawing.Point(16, 14)
$refreshAnalogButton.Size = New-Object System.Drawing.Size(130, 32)
$analogToolbar.Controls.Add($refreshAnalogButton)

$analogHint = New-Object System.Windows.Forms.Label
$analogHint.Text = "读取将自动把 GPIO1–18 切换到模拟输入模式"
$analogHint.Location = New-Object System.Drawing.Point(170, 22)
$analogHint.AutoSize = $true
$analogHint.ForeColor = [System.Drawing.Color]::FromArgb(95, 105, 120)
$analogToolbar.Controls.Add($analogHint)

$script:AnalogGrid = New-Object System.Windows.Forms.DataGridView
$script:AnalogGrid.Dock = [System.Windows.Forms.DockStyle]::Fill
$script:AnalogGrid.AllowUserToAddRows = $false
$script:AnalogGrid.AllowUserToDeleteRows = $false
$script:AnalogGrid.ReadOnly = $true
$script:AnalogGrid.RowHeadersVisible = $false
$script:AnalogGrid.AutoSizeColumnsMode = [System.Windows.Forms.DataGridViewAutoSizeColumnsMode]::Fill
$script:AnalogGrid.SelectionMode = [System.Windows.Forms.DataGridViewSelectionMode]::FullRowSelect
$script:AnalogGrid.BackgroundColor = [System.Drawing.Color]::White
[void]$script:AnalogGrid.Columns.Add("channel", "模拟通道")
[void]$script:AnalogGrid.Columns.Add("gpio", "GPIO")
[void]$script:AnalogGrid.Columns.Add("raw", "ADC 原始值")
[void]$script:AnalogGrid.Columns.Add("mv", "校准电压")
$analogTab.Controls.Add($script:AnalogGrid)
$script:AnalogGrid.BringToFront()

$logTab = New-Object System.Windows.Forms.TabPage
$logTab.Text = "运行日志"
$logTab.BackColor = [System.Drawing.Color]::White
$tabs.TabPages.Add($logTab)

$script:LogBox = New-Object System.Windows.Forms.TextBox
$script:LogBox.Dock = [System.Windows.Forms.DockStyle]::Fill
$script:LogBox.Multiline = $true
$script:LogBox.ReadOnly = $true
$script:LogBox.ScrollBars = [System.Windows.Forms.ScrollBars]::Vertical
$script:LogBox.BackColor = [System.Drawing.Color]::FromArgb(247, 249, 252)
$script:LogBox.Font = New-Object System.Drawing.Font("Consolas", 10)
$logTab.Controls.Add($script:LogBox)

$refreshPortsButton.Add_Click({ Refresh-PortList })
$connectButton.Add_Click({ Invoke-UiAction "读取设备信息" { Read-DeviceInfo } })
$refreshDigitalButton.Add_Click({ Invoke-UiAction "刷新数字 IO" { Refresh-DigitalTable } })
$refreshAnalogButton.Add_Click({ Invoke-UiAction "读取全部 ADC" { Refresh-AnalogTable } })

$setModeButton.Add_Click({
    Invoke-UiAction "设置通道模式" {
        Write-ModbusRegister ([uint16]$script:ChannelBox.Value) ([uint16]$script:ModeBox.SelectedIndex)
        Refresh-DigitalTable
    }
})

$highButton.Add_Click({
    Invoke-UiAction "输出高电平" {
        Write-ModbusCoil ([uint16]$script:ChannelBox.Value) $true
        Refresh-DigitalTable
    }
})

$lowButton.Add_Click({
    Invoke-UiAction "输出低电平" {
        Write-ModbusCoil ([uint16]$script:ChannelBox.Value) $false
        Refresh-DigitalTable
    }
})

$script:DigitalGrid.Add_CellClick({
    param($sender, $eventArgs)
    if ($eventArgs.RowIndex -ge 0) {
        $script:ChannelBox.Value = [int]$script:DigitalGrid.Rows[$eventArgs.RowIndex].Cells[0].Value
    }
})

$script:Form.Add_Shown({
    Refresh-PortList
    if ($script:PortBox.Items.Count -gt 0) {
        Invoke-UiAction "自动连接" { Read-DeviceInfo }
    }
})

[void][System.Windows.Forms.Application]::Run($script:Form)
