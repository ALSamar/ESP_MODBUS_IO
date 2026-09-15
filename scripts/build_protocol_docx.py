from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION_START
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK, WD_LINE_SPACING
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Inches, Pt, RGBColor


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "ESP32-S3_USB_Modbus_IO_Protocol.docx"
BOARD_IMAGE = Path(
    r"C:\Users\al182\AppData\Local\Temp\codex-clipboard-cffef77b-80c4-43f5-947d-564f35cb61a6.png"
)

NAVY = "1F4E78"
PALE_BLUE = "EAF2F8"
LIGHT_GRAY = "D9D9D9"
CODE_FILL = "F3F5F7"
BLACK = RGBColor(0, 0, 0)


DIGITAL_CHANNELS = [
    (0, 0, "启动绑带和 BOOT 按键"),
    (1, 1, "ADC 模拟通道 0"),
    (2, 2, "ADC 模拟通道 1"),
    (3, 3, "ADC 模拟通道 2，启动绑带"),
    (4, 4, "ADC 模拟通道 3"),
    (5, 5, "ADC 模拟通道 4"),
    (6, 6, "ADC 模拟通道 5"),
    (7, 7, "ADC 模拟通道 6"),
    (8, 8, "ADC 模拟通道 7"),
    (9, 9, "ADC 模拟通道 8"),
    (10, 10, "ADC 模拟通道 9"),
    (11, 11, "ADC 模拟通道 10"),
    (12, 12, "ADC 模拟通道 11"),
    (13, 13, "ADC 模拟通道 12"),
    (14, 14, "ADC 模拟通道 13"),
    (15, 15, "ADC 模拟通道 14"),
    (16, 16, "ADC 模拟通道 15"),
    (17, 17, "ADC 模拟通道 16"),
    (18, 18, "ADC 模拟通道 17"),
    (19, 21, "通用 IO"),
    (20, 38, "通用 IO"),
    (21, 39, "JTAG 复用"),
    (22, 40, "JTAG 复用"),
    (23, 41, "JTAG 复用"),
    (24, 42, "JTAG 复用"),
    (25, 43, "板上标记 TX，UART0 复用"),
    (26, 44, "板上标记 RX，UART0 复用"),
    (27, 45, "启动绑带"),
    (28, 46, "启动绑带"),
    (29, 47, "通用 IO"),
    (30, 48, "通常连接板载 RGB LED"),
    (31, 35, "默认禁用，可能连接八线 PSRAM"),
    (32, 36, "默认禁用，可能连接八线 PSRAM"),
    (33, 37, "默认禁用，可能连接八线 PSRAM"),
]


def set_cell_shading(cell, fill: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shading = tc_pr.find(qn("w:shd"))
    if shading is None:
        shading = OxmlElement("w:shd")
        tc_pr.append(shading)
    shading.set(qn("w:fill"), fill)


def set_cell_margins(cell, top=90, start=110, bottom=90, end=110) -> None:
    tc = cell._tc
    tc_pr = tc.get_or_add_tcPr()
    margins = tc_pr.first_child_found_in("w:tcMar")
    if margins is None:
        margins = OxmlElement("w:tcMar")
        tc_pr.append(margins)
    for tag, value in (("top", top), ("start", start), ("bottom", bottom), ("end", end)):
        node = margins.find(qn(f"w:{tag}"))
        if node is None:
            node = OxmlElement(f"w:{tag}")
            margins.append(node)
        node.set(qn("w:w"), str(value))
        node.set(qn("w:type"), "dxa")


def set_repeat_table_header(row) -> None:
    tr_pr = row._tr.get_or_add_trPr()
    header = OxmlElement("w:tblHeader")
    header.set(qn("w:val"), "true")
    tr_pr.append(header)


def set_cant_split(row) -> None:
    tr_pr = row._tr.get_or_add_trPr()
    cant_split = OxmlElement("w:cantSplit")
    tr_pr.append(cant_split)


def set_cell_width(cell, width_inches: float) -> None:
    cell.width = Inches(width_inches)
    tc_pr = cell._tc.get_or_add_tcPr()
    tc_w = tc_pr.find(qn("w:tcW"))
    if tc_w is None:
        tc_w = OxmlElement("w:tcW")
        tc_pr.append(tc_w)
    tc_w.set(qn("w:w"), str(int(width_inches * 1440)))
    tc_w.set(qn("w:type"), "dxa")


def style_run(run, *, font="Microsoft YaHei", size=10.5, bold=False, color=BLACK) -> None:
    run.font.name = font
    run._element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), font)
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color


def add_hyperlink(paragraph, text: str, url: str) -> None:
    part = paragraph.part
    relationship_id = part.relate_to(
        url,
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
        is_external=True,
    )
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), relationship_id)
    new_run = OxmlElement("w:r")
    r_pr = OxmlElement("w:rPr")
    color = OxmlElement("w:color")
    color.set(qn("w:val"), "0563C1")
    underline = OxmlElement("w:u")
    underline.set(qn("w:val"), "single")
    fonts = OxmlElement("w:rFonts")
    fonts.set(qn("w:ascii"), "Microsoft YaHei")
    fonts.set(qn("w:hAnsi"), "Microsoft YaHei")
    fonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    size = OxmlElement("w:sz")
    size.set(qn("w:val"), "18")
    r_pr.extend((fonts, color, underline, size))
    new_run.append(r_pr)
    text_node = OxmlElement("w:t")
    text_node.text = text
    new_run.append(text_node)
    hyperlink.append(new_run)
    paragraph._p.append(hyperlink)


def set_table_borders(table) -> None:
    tbl_pr = table._tbl.tblPr
    borders = tbl_pr.find(qn("w:tblBorders"))
    if borders is None:
        borders = OxmlElement("w:tblBorders")
        tbl_pr.append(borders)
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        tag = borders.find(qn(f"w:{edge}"))
        if tag is None:
            tag = OxmlElement(f"w:{edge}")
            borders.append(tag)
        tag.set(qn("w:val"), "single")
        tag.set(qn("w:sz"), "6")
        tag.set(qn("w:space"), "0")
        tag.set(qn("w:color"), LIGHT_GRAY)


def add_table(doc, headers, rows, widths, alignments=None, font_size=9.2):
    table = doc.add_table(rows=1, cols=len(headers))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    set_table_borders(table)
    header = table.rows[0]
    set_repeat_table_header(header)
    set_cant_split(header)
    for index, label in enumerate(headers):
        cell = header.cells[index]
        set_cell_width(cell, widths[index])
        set_cell_shading(cell, NAVY)
        set_cell_margins(cell)
        cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        paragraph = cell.paragraphs[0]
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        paragraph.paragraph_format.space_before = Pt(0)
        paragraph.paragraph_format.space_after = Pt(0)
        style_run(paragraph.add_run(str(label)), size=9.3, bold=True, color=RGBColor(255, 255, 255))

    for row_index, values in enumerate(rows):
        row = table.add_row()
        set_cant_split(row)
        for column, value in enumerate(values):
            cell = row.cells[column]
            set_cell_width(cell, widths[column])
            if row_index % 2 == 1:
                set_cell_shading(cell, PALE_BLUE)
            set_cell_margins(cell)
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            paragraph = cell.paragraphs[0]
            paragraph.paragraph_format.space_before = Pt(0)
            paragraph.paragraph_format.space_after = Pt(0)
            paragraph.paragraph_format.line_spacing = 1.08
            if alignments:
                paragraph.alignment = alignments[column]
            style_run(paragraph.add_run(str(value)), size=font_size)
    doc.add_paragraph().paragraph_format.space_after = Pt(0)
    return table


def add_body(doc, text: str, *, bold_lead: str | None = None):
    paragraph = doc.add_paragraph(style="Body Text")
    paragraph.paragraph_format.keep_together = False
    if bold_lead:
        paragraph.paragraph_format.keep_with_next = True
        style_run(paragraph.add_run(bold_lead), bold=True)
    style_run(paragraph.add_run(text))
    return paragraph


def add_bullets(doc, items):
    for item in items:
        paragraph = doc.add_paragraph(style="List Bullet")
        style_run(paragraph.add_run(item))


def add_code(doc, lines):
    for line in lines:
        paragraph = doc.add_paragraph(style="Code")
        paragraph.paragraph_format.keep_together = True
        paragraph.paragraph_format.space_before = Pt(0)
        paragraph.paragraph_format.space_after = Pt(0)
        p_pr = paragraph._p.get_or_add_pPr()
        shading = OxmlElement("w:shd")
        shading.set(qn("w:fill"), CODE_FILL)
        p_pr.append(shading)
        style_run(paragraph.add_run(line), font="Cascadia Mono", size=9.2)
    spacer = doc.add_paragraph()
    spacer.paragraph_format.space_after = Pt(0)


def add_page_number(paragraph) -> None:
    paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    run = paragraph.add_run()
    style_run(run, size=9)
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instruction = OxmlElement("w:instrText")
    instruction.set(qn("xml:space"), "preserve")
    instruction.text = " PAGE "
    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    run._r.extend((begin, instruction, separate, end))


def configure_document(doc: Document) -> None:
    section = doc.sections[0]
    section.page_width = Inches(8.5)
    section.page_height = Inches(11)
    section.top_margin = Inches(0.68)
    section.bottom_margin = Inches(0.62)
    section.left_margin = Inches(0.72)
    section.right_margin = Inches(0.72)
    section.header_distance = Inches(0.3)
    section.footer_distance = Inches(0.32)

    styles = doc.styles
    normal = styles["Normal"]
    normal.font.name = "Microsoft YaHei"
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    normal.font.size = Pt(10.5)
    normal.font.color.rgb = BLACK
    normal.paragraph_format.space_after = Pt(5)
    normal.paragraph_format.line_spacing = 1.18

    body = styles["Body Text"]
    body.base_style = normal
    body.paragraph_format.first_line_indent = Cm(0.74)
    body.paragraph_format.space_after = Pt(6)
    body.paragraph_format.line_spacing = 1.2

    title = styles["Title"]
    title.font.name = "Microsoft YaHei"
    title._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    title.font.size = Pt(24)
    title.font.bold = True
    title.font.color.rgb = BLACK
    title.paragraph_format.space_after = Pt(8)
    title_ppr = title._element.get_or_add_pPr()
    title_border = title_ppr.find(qn("w:pBdr"))
    if title_border is not None:
        title_ppr.remove(title_border)

    subtitle = styles["Subtitle"]
    subtitle.font.name = "Microsoft YaHei"
    subtitle._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    subtitle.font.size = Pt(12)
    subtitle.font.color.rgb = BLACK
    subtitle.paragraph_format.space_after = Pt(10)

    for style_name, size, before, after in (
        ("Heading 1", 16, 14, 7),
        ("Heading 2", 12.5, 10, 5),
        ("Heading 3", 11, 8, 4),
    ):
        style = styles[style_name]
        style.font.name = "Microsoft YaHei"
        style._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
        style.font.size = Pt(size)
        style.font.bold = True
        style.font.color.rgb = BLACK
        style.paragraph_format.space_before = Pt(before)
        style.paragraph_format.space_after = Pt(after)
        style.paragraph_format.keep_with_next = True

    list_bullet = styles["List Bullet"]
    list_bullet.base_style = normal
    list_bullet.paragraph_format.left_indent = Cm(0.74)
    list_bullet.paragraph_format.first_line_indent = Cm(-0.42)
    list_bullet.paragraph_format.space_after = Pt(3)

    code = styles.add_style("Code", 1)
    code.font.name = "Cascadia Mono"
    code._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    code.font.size = Pt(9.2)
    code.font.color.rgb = BLACK
    code.paragraph_format.left_indent = Cm(0.35)
    code.paragraph_format.right_indent = Cm(0.35)
    code.paragraph_format.line_spacing = 1.05

    footer_table = section.footer.add_table(rows=1, cols=2, width=Inches(7.06))
    footer_table.autofit = False
    footer_table.alignment = WD_TABLE_ALIGNMENT.CENTER
    left, right = footer_table.rows[0].cells
    set_cell_width(left, 5.8)
    set_cell_width(right, 1.26)
    style_run(left.paragraphs[0].add_run("ESP32-S3 USB Modbus IO 协议 1.0"), size=8.5)
    add_page_number(right.paragraphs[0])


def build() -> None:
    doc = Document()
    configure_document(doc)
    doc.core_properties.title = "ESP32-S3 USB Modbus IO 通信协议"
    doc.core_properties.subject = "ESP-IDF 固件通信与寄存器说明"
    doc.core_properties.author = "ESP32-S3 USB Modbus IO Project"
    doc.core_properties.keywords = "ESP32-S3, ESP-IDF, Modbus RTU, USB CDC, GPIO, ADC"

    title = doc.add_paragraph(style="Title")
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    style_run(title.add_run("ESP32-S3 USB Modbus IO 通信协议"), size=24, bold=True)
    subtitle = doc.add_paragraph(style="Subtitle")
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    style_run(subtitle.add_run("ESP-IDF 固件通信与寄存器说明"), size=12)

    meta = doc.add_paragraph()
    meta.alignment = WD_ALIGN_PARAGRAPH.CENTER
    style_run(meta.add_run("协议版本 1.0    固件版本 1.0    从站地址 1"), size=10, bold=True)

    add_body(
        doc,
        "本手册给出图示 ESP32-S3-WROOM-1 开发板的 USB 虚拟串口 Modbus RTU 接口。"
        "主站可读取数字输入和输出状态、控制数字输出、读取 GPIO1 到 GPIO18 的 ADC 数据，"
        "并通过保持寄存器设置每个通道的工作模式。",
    )

    if BOARD_IMAGE.exists():
        image_paragraph = doc.add_paragraph()
        image_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        picture = image_paragraph.add_run().add_picture(str(BOARD_IMAGE), width=Inches(5.35))
        picture._inline.docPr.set("descr", "ESP32-S3-WROOM-1 双 USB-C 开发板引脚图")
        caption = doc.add_paragraph()
        caption.alignment = WD_ALIGN_PARAGRAPH.CENTER
        style_run(caption.add_run("图 1  ESP32-S3-WROOM-1 开发板引脚"), size=9)

    doc.add_page_break()

    doc.add_heading("1 快速使用", level=1)
    add_body(
        doc,
        "通信线应连接到 GPIO19 和 GPIO20 对应的原生 USB 或 OTG 接口。另一 USB-C 接口如果经过"
        "USB 转串口芯片，只能用于 UART 烧录或调试，不承载本协议。USB CDC 的波特率设置不影响"
        "实际 USB 传输，但主站仍需发送完整的 Modbus RTU 帧和 CRC。",
    )
    add_table(
        doc,
        ["项目", "默认值", "说明"],
        [
            ["目标芯片", "ESP32-S3", "ESP32-S3-WROOM-1 开发板"],
            ["传输", "USB CDC-ACM", "原生 USB D- GPIO19 和 D+ GPIO20"],
            ["协议", "Modbus RTU", "CRC16 低字节先发送"],
            ["从站地址", "1", "可在 menuconfig 中改为 1 到 247"],
            ["不完整帧超时", "20 ms", "完整帧立即处理"],
            ["数字通道", "34", "默认可用 31 个，GPIO35 到 GPIO37 保留"],
            ["模拟通道", "18", "GPIO1 到 GPIO18"],
        ],
        [1.45, 1.55, 4.0],
        [WD_ALIGN_PARAGRAPH.LEFT, WD_ALIGN_PARAGRAPH.CENTER, WD_ALIGN_PARAGRAPH.LEFT],
    )
    add_code(
        doc,
        [
            "idf.py set-target esp32s3",
            "idf.py build",
            "idf.py -p COMx flash",
            "python -m pip install -r requirements.txt",
            "python tools/modbus_usb_client.py --port COM8 info",
        ],
    )

    doc.add_heading("2 硬件和电气要求", level=1)
    add_bullets(
        doc,
        [
            "GPIO 逻辑电平为 3.3 V，不得直接输入 5 V。",
            "GPIO1 到 GPIO18 支持 ADC。建议使用低阻抗信号源，并保证输入始终处于芯片允许范围。",
            "继电器、线圈、电机、灯带和其他大电流负载必须通过驱动器和保护电路连接。",
            "GPIO0、GPIO3、GPIO45、GPIO46 是启动绑带引脚，外部电路不能在复位期间强迫错误电平。",
            "GPIO0 通常连接 BOOT 按键，GPIO48 通常连接板载 RGB LED。",
            "GPIO35 到 GPIO37 可能连接八线 PSRAM，默认禁用。只有确认模组接线兼容后才能启用。",
        ],
    )

    heading = doc.add_heading("3 RTU 帧和 USB 传输", level=1)
    heading.paragraph_format.page_break_before = True
    add_table(
        doc,
        ["字段", "长度", "说明"],
        [
            ["从站地址", "1 字节", "默认 0x01；地址 0 仅用于广播写"],
            ["功能码", "1 字节", "见第 4 节"],
            ["数据", "可变", "多字节数值高字节在前"],
            ["CRC16", "2 字节", "多项式 0xA001，低字节先发送"],
        ],
        [1.35, 1.1, 4.55],
        [WD_ALIGN_PARAGRAPH.LEFT, WD_ALIGN_PARAGRAPH.CENTER, WD_ALIGN_PARAGRAPH.LEFT],
    )
    add_body(
        doc,
        "USB 可能拆分或合并串口写操作。固件按功能码和长度字段重组请求。完整帧立即处理；"
        "不完整字节流超过配置超时后丢弃。主站应在一次写操作中提交完整 RTU 帧，并等待响应后"
        "再发送下一条请求。地址 0 的合法写请求会执行但不响应，广播读会被忽略。",
    )

    doc.add_heading("4 功能码", level=1)
    add_table(
        doc,
        ["功能码", "名称", "用途"],
        [
            ["0x01", "Read Coils", "读取数字输出命令状态"],
            ["0x02", "Read Discrete Inputs", "读取 GPIO 实际逻辑电平"],
            ["0x03", "Read Holding Registers", "读取模式、GPIO 映射和设备信息"],
            ["0x04", "Read Input Registers", "读取 ADC 原始值或校准毫伏值"],
            ["0x05", "Write Single Coil", "设置一个数字输出并自动进入输出模式"],
            ["0x06", "Write Single Register", "设置一个 GPIO 模式"],
            ["0x0F", "Write Multiple Coils", "批量设置数字输出"],
            ["0x10", "Write Multiple Registers", "批量设置连续通道模式"],
        ],
        [1.0, 2.45, 3.55],
        [WD_ALIGN_PARAGRAPH.CENTER, WD_ALIGN_PARAGRAPH.LEFT, WD_ALIGN_PARAGRAPH.LEFT],
    )

    doc.add_heading("5 数字通道映射", level=1)
    add_body(
        doc,
        "Modbus PDU 地址从 0 开始，传统线圈号和离散输入号从 1 开始显示。线圈返回最近写入的"
        "输出命令状态，离散输入返回引脚实际电平。GPIO35 到 GPIO37 的通道号始终保留，但默认"
        "访问会返回非法数据地址异常。",
    )
    digital_rows = []
    for channel, gpio, note in DIGITAL_CHANNELS:
        default = "禁用" if channel >= 31 else "浮空输入"
        digital_rows.append(
            [channel, gpio, f"{channel + 1:05d}", f"{10001 + channel}", default, note]
        )
    add_table(
        doc,
        ["通道", "GPIO", "线圈号", "离散输入号", "默认", "备注"],
        digital_rows,
        [0.55, 0.6, 0.85, 1.05, 0.9, 3.05],
        [
            WD_ALIGN_PARAGRAPH.CENTER,
            WD_ALIGN_PARAGRAPH.CENTER,
            WD_ALIGN_PARAGRAPH.CENTER,
            WD_ALIGN_PARAGRAPH.CENTER,
            WD_ALIGN_PARAGRAPH.CENTER,
            WD_ALIGN_PARAGRAPH.LEFT,
        ],
        font_size=8.7,
    )

    doc.add_heading("6 模拟输入寄存器", level=1)
    add_body(
        doc,
        "模拟通道 0 到 17 依次对应 GPIO1 到 GPIO18。ADC 使用 12 dB 衰减和默认位宽。读取时会"
        "把非输出 GPIO 切换为模拟模式。如果对应 GPIO 正在输出，固件返回异常 0x04，避免改变"
        "现有输出。",
    )
    add_table(
        doc,
        ["PDU 地址", "传统寄存器号", "内容", "数值"],
        [
            ["0x0000 到 0x0011", "30001 到 30018", "ADC 原始值", "典型范围 0 到 4095"],
            ["0x0100 到 0x0111", "30257 到 30274", "校准电压", "mV；无校准时 0xFFFF"],
        ],
        [1.65, 1.55, 1.4, 2.4],
        [WD_ALIGN_PARAGRAPH.CENTER, WD_ALIGN_PARAGRAPH.CENTER,
         WD_ALIGN_PARAGRAPH.LEFT, WD_ALIGN_PARAGRAPH.LEFT],
    )
    add_body(
        doc,
        "校准毫伏值来自 ESP-IDF ADC 曲线拟合校准接口。没有校准数据时，原始寄存器仍可读取，"
        "毫伏寄存器返回 0xFFFF。ADC 结果会受信号源阻抗、噪声、板级布局和芯片误差影响。",
    )

    heading = doc.add_heading("7 保持寄存器", level=1)
    heading.paragraph_format.page_break_before = True
    doc.add_heading("7 1 GPIO 模式", level=2)
    add_body(
        doc,
        "PDU 地址 0x0000 到 0x0021 对应数字通道 0 到 33。可用 0x03 读取，用 0x06 或 0x10 写入。"
        "写线圈会自动切换到输出模式；切换为其他模式会关闭输出驱动。",
    )
    add_table(
        doc,
        ["模式值", "含义", "限制"],
        [
            [0, "浮空数字输入", "所有可用通道"],
            [1, "上拉数字输入", "所有可用通道"],
            [2, "下拉数字输入", "所有可用通道"],
            [3, "数字输出", "保留最近一次线圈命令值"],
            [4, "模拟输入", "仅数字通道 1 到 18"],
        ],
        [1.0, 2.2, 3.8],
        [WD_ALIGN_PARAGRAPH.CENTER, WD_ALIGN_PARAGRAPH.LEFT, WD_ALIGN_PARAGRAPH.LEFT],
    )
    doc.add_heading("7 2 GPIO 映射", level=2)
    add_body(
        doc,
        "PDU 地址 0x0100 到 0x0121 只读，对应数字通道 0 到 33。寄存器值是实际 GPIO 编号。"
        "默认禁用的 GPIO35 到 GPIO37 返回非法数据地址异常。",
    )
    doc.add_heading("7 3 设备信息", level=2)
    add_table(
        doc,
        ["PDU 地址", "传统寄存器号", "内容", "当前值"],
        [
            ["0x0200", "40513", "协议版本", "0x0100 表示 1.0"],
            ["0x0201", "40514", "固件版本", "0x0100 表示 1.0"],
            ["0x0202", "40515", "数字通道总数", "34"],
            ["0x0203", "40516", "模拟通道总数", "18"],
            ["0x0204", "40517", "能力位", "bit0 ADC 校准；bit1 GPIO35 到 GPIO37"],
        ],
        [1.0, 1.2, 1.8, 3.0],
        [WD_ALIGN_PARAGRAPH.CENTER, WD_ALIGN_PARAGRAPH.CENTER,
         WD_ALIGN_PARAGRAPH.LEFT, WD_ALIGN_PARAGRAPH.LEFT],
    )

    doc.add_heading("8 请求与响应规则", level=1)
    add_body(doc, "功能码 0x01 和 0x02 的位从响应数据字节最低位开始排列，未使用的最高位填 0。")
    add_body(doc, "功能码 0x05 使用 0xFF00 表示高电平，使用 0x0000 表示低电平。其他值返回异常 0x03。")
    add_body(doc, "功能码 0x03 和 0x04 的寄存器按高字节在前排列。0x06 只允许写 GPIO 模式地址。")
    add_body(doc, "功能码 0x0F 按最低位优先携带线圈值。0x10 的每个模式占 2 字节。固件先验证完整请求，再执行批量写。")

    doc.add_heading("9 异常响应", level=1)
    add_table(
        doc,
        ["异常码", "名称", "含义"],
        [
            ["0x01", "Illegal Function", "不支持该功能码"],
            ["0x02", "Illegal Data Address", "地址越界、通道禁用或能力不匹配"],
            ["0x03", "Illegal Data Value", "数量、字节数、线圈编码或模式值无效"],
            ["0x04", "Server Device Failure", "模式冲突或底层 GPIO ADC 操作失败"],
        ],
        [1.0, 2.1, 3.9],
        [WD_ALIGN_PARAGRAPH.CENTER, WD_ALIGN_PARAGRAPH.LEFT, WD_ALIGN_PARAGRAPH.LEFT],
    )
    add_body(doc, "CRC 错误、发给其他从站的请求和不完整超时帧不会产生响应。")

    doc.add_heading("10 报文示例", level=1)
    add_body(doc, "以下十六进制字节均包含 CRC，CRC 低字节在前。")
    examples = [
        ("读取 5 个设备信息寄存器", ["请求  01 03 02 00 00 05 84 71"]),
        ("读取数字通道 0 到 7 的实际电平", ["请求  01 02 00 00 00 08 79 CC", "响应  01 02 01 XX CRC_LO CRC_HI"]),
        ("把数字通道 1 设置为输出模式", ["请求  01 06 00 01 00 03 98 0B", "响应  01 06 00 01 00 03 98 0B"]),
        ("把数字通道 1 对应的 GPIO1 输出高电平", ["请求  01 05 00 01 FF 00 DD FA", "响应  01 05 00 01 FF 00 DD FA"]),
        ("读取模拟通道 0 的原始值", ["请求  01 04 00 00 00 01 31 CA", "响应  01 04 02 RAW_H RAW_L CRC_LO CRC_HI"]),
        ("读取模拟通道 0 的校准毫伏值", ["请求  01 04 01 00 00 01 30 36", "响应  01 04 02 MV_H MV_L CRC_LO CRC_HI"]),
    ]
    for label, lines in examples:
        add_body(doc, "", bold_lead=label)
        add_code(doc, lines)

    doc.add_heading("11 主机端测试", level=1)
    add_body(doc, "仓库中的 tools/modbus_usb_client.py 提供常用命令。安装 pyserial 后指定 CDC 串口。")
    add_code(
        doc,
        [
            "python -m pip install -r requirements.txt",
            "python tools/modbus_usb_client.py --port COM8 info",
            "python tools/modbus_usb_client.py --port COM8 read-map 0 31",
            "python tools/modbus_usb_client.py --port COM8 read-inputs 0 8",
            "python tools/modbus_usb_client.py --port COM8 write-output 1 on",
            "python tools/modbus_usb_client.py --port COM8 read-analog 0 4",
            "python tools/modbus_usb_client.py --port COM8 read-analog 0 4 --raw",
            "python tools/modbus_usb_client.py --port COM8 set-mode 1 0",
        ],
    )

    doc.add_heading("12 构建配置", level=1)
    add_body(
        doc,
        "工程支持 ESP-IDF 5.4 和 5.5。首次构建时组件管理器下载 espressif/esp_tinyusb。菜单 USB Modbus IO"
        " 可设置从站地址、不完整帧超时和 GPIO35 到 GPIO37 开关。工程默认关闭控制台，避免 UART0 或"
        " USB 日志污染 Modbus 数据，并释放 GPIO43 和 GPIO44。",
    )
    add_code(doc, ["idf.py set-target esp32s3", "idf.py menuconfig", "idf.py build", "idf.py -p COMx flash"])

    references_heading = doc.add_heading("13 参考资料", level=1)
    references_heading.paragraph_format.space_before = Pt(8)
    references_heading.paragraph_format.space_after = Pt(3)
    references = [
        ("ESP-IDF USB Device Stack", "https://docs.espressif.com/projects/esp-idf/en/v5.5/esp32s3/api-reference/peripherals/usb_device.html"),
        ("ESP-IDF ADC Oneshot Driver", "https://docs.espressif.com/projects/esp-idf/en/v5.5/esp32s3/api-reference/peripherals/adc_oneshot.html"),
        ("ESP32-S3 Datasheet", "https://documentation.espressif.com/esp32_s3_datasheet_en.pdf"),
        ("Modbus Application Protocol V1.1b3", "https://www.modbus.org/docs/Modbus_Application_Protocol_V1_1b3.pdf"),
        ("Modbus Serial Line Guide V1.02", "https://www.modbus.org/docs/Modbus_over_serial_line_V1_02.pdf"),
    ]
    for label, url in references:
        paragraph = doc.add_paragraph(style="List Bullet")
        paragraph.paragraph_format.space_after = Pt(0)
        paragraph.paragraph_format.line_spacing = 1.0
        add_hyperlink(paragraph, label, url)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    doc.save(OUTPUT)
    print(OUTPUT)


if __name__ == "__main__":
    build()
