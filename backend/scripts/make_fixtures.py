# -*- coding: utf-8 -*-
"""
生成本地评测与演示用的样例文件。

职责：用纯代码造出一批体量适中、可离线复现的样例，覆盖两条管线要验证的每种输入。
输出目录：backend/data/（与随仓库提供的文本类样例同级，不再多套一层子目录）

步骤：
    step1_make_images        截图（有字）与照片（无字），后续步骤会内嵌它们
    step2_make_docx          带表格与内嵌图的 Word 文档
    step3_make_xlsx          单表、含空单元格的 Excel
    step4_make_pptx          六页演示文稿，含表格与图片
    step5_make_pdf           十页纯文本 PDF
    step6_make_scanned_pdf   三页「扫描件」：整页是图、没有文字层
    step7_make_archive       十几个条目的 ZIP
    step8_make_install_pkg   APK 结构指纹样本
    step9_make_legacy_office 老版 .doc 的头部样本，用于验证降级路径
    step10_make_odf          odt / odp 最小可用文档（标准库拼 ODF 包，不依赖 odfpy）
    step11_make_audio        16kHz 单声道 wav 样本，验证音频的结构信息与「送本机 ASR」
    step12_make_video        把音频样本封成 mp4，验证视频要先抽音轨那条链
    step13_make_merged       含合并单元格的 docx / xlsx / pptx，验证表格格位建模
    step14_make_repeated_images  同一张图被引多次的 docx / pptx / pdf，验证内嵌图按来源部件去重
    step15_make_structured_odt   带合并表格与两级列表的 odt，验证 ODT 按结构抽取
    step16_make_docx_hyperlinks  含超链接的 docx，验证链接的文字与地址都进交付文本

运行方式：在 backend/ 下执行 python scripts/make_fixtures.py（无参数；`__main__` 只按序号调用各 step）
使用前注意：
- 需要先装好后端依赖（python-docx / python-pptx / openpyxl / pymupdf / pillow）。
- 造出的 .apk 与 .doc 只是**结构样本**，不是真的安装包与 Word 文档，
  用途是验证类型识别与降级分支，不要拿它们做别的判断。
- 造出的音视频**没有语音内容**（一段正弦音），只用于验证结构信息与转码链；
  要验收转写质量请自己准备一段真实录音。
- 阶段 12 的 mp4 需要 imageio-ffmpeg（可选依赖），没装时跳过并说明。
"""
import math
import struct
import subprocess
import sys
import wave
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BACKEND_DIR = Path(__file__).resolve().parent.parent  # backend/
DATA_DIR = BACKEND_DIR / "data"

# 造页面图用的画布与字号
PAGE_WIDTH = 1240
PAGE_HEIGHT = 1754
PAGE_MARGIN = 90
FONT_SIZE = 40

CJK_FONT_CANDIDATES = (
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
)

# 页面正文（中文）。取不到中文字体时退回等价的英文内容，保证脚本在无中文字体环境也能跑。
PAGE_LINES_CJK = [
    "秋季越野赛竞赛规程",
    "",
    "一、主办与承办",
    "本次比赛由示例体育协会主办，示例赛事服务有限公司承办。",
    "",
    "二、时间与地点",
    "比赛时间：2026 年 10 月 25 日 08:00 发枪。",
    "比赛地点：示例城市滨江公园东门广场。",
    "报到时间：2026 年 10 月 24 日 09:00 至 20:00。",
    "",
    "三、组别与关门时间",
    "十公里组：关门时间 3 小时，限报 800 人。",
    "二十一公里组：关门时间 6 小时，限报 500 人。",
    "四十二公里组：关门时间 9 小时，限报 300 人。",
    "",
    "四、参赛资格",
    "年满十八周岁，身体健康，无心血管疾病史。",
    "四十二公里组需提供近一年内半程马拉松完赛证明。",
    "",
    "五、报名与缴费",
    "报名截止时间：2026 年 10 月 15 日 18:00。",
    "报名费：十公里 80 元，二十一公里 150 元，四十二公里 260 元。",
    "",
    "六、联系方式",
    "咨询电话：000-0000-0000",
    "邮箱：race@example.com",
]
PAGE_LINES_ASCII = [
    "Autumn Trail Race - Rules",
    "",
    "1. Organizer",
    "Hosted by the Sample Sports Association.",
    "",
    "2. Date and place",
    "Start: 2026-10-25 08:00. Venue: Riverside Park East Gate.",
    "Check-in: 2026-10-24 09:00 to 20:00.",
    "",
    "3. Categories and cut-off",
    "10 km: cut-off 3 h, limit 800 runners.",
    "21 km: cut-off 6 h, limit 500 runners.",
    "42 km: cut-off 9 h, limit 300 runners.",
    "",
    "4. Eligibility",
    "Age 18 or above, in good health.",
    "42 km requires a half-marathon finish certificate.",
    "",
    "5. Entry fee",
    "10 km: 80 CNY. 21 km: 150 CNY. 42 km: 260 CNY.",
    "",
    "6. Contact",
    "Phone: 000-0000-0000  Email: race@example.com",
]

ODT_MIMETYPE = "application/vnd.oasis.opendocument.text"
ODP_MIMETYPE = "application/vnd.oasis.opendocument.presentation"

# 音频样例参数：16kHz 单声道 16bit 是语音识别的输入规格，3 秒够验证整条链又不占体积
AUDIO_SAMPLE_RATE = 16000
AUDIO_SECONDS = 3
AUDIO_TONE_HZ = 440
AUDIO_AMPLITUDE = 12000

# 合并单元格样例：三种格式用同一套结构与同一批固定文本，文本取成可数的串。
# xlsx 多一张「合并区是唯一尾部内容」的表，用来验证覆盖位占列位、尾列不被裁掉。
MERGED_TITLE = "合并标题"
MERGED_VERTICAL = "纵向合并"
MERGED_TAIL = "尾列合并"
MERGED_COLUMNS = 3

# 内嵌图重复引用样例：同一张图被引 3 次，再引另一张一次（共 4 处引用、2 张不同的图）。
# 用来验证「按来源部件去重」——修前重复引用会按出现次序占满 MAX_EMBEDDED_IMAGES 名额，
# 后面的图因此一次都读不到，而落下的 3 份文件内容一模一样。
REPEATED_IMAGE_REPEATS = 3

ODF_MANIFEST = """<?xml version="1.0" encoding="UTF-8"?>
<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.2">
 <manifest:file-entry manifest:full-path="/" manifest:version="1.2" manifest:media-type="{mimetype}"/>
 <manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>
 <manifest:file-entry manifest:full-path="styles.xml" manifest:media-type="text/xml"/>
</manifest:manifest>
"""

ODF_STYLES_XML = """<?xml version="1.0" encoding="UTF-8"?>
<office:document-styles xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" office:version="1.2">
 <office:styles/>
</office:document-styles>
"""

ODT_CONTENT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" office:version="1.2">
 <office:body>
  <office:text>
   <text:h text:outline-level="1">秋季越野赛竞赛规程</text:h>
   <text:p>本文件用于验证 ODT 抽取：标题层级与段落顺序都要保留。</text:p>
   <text:h text:outline-level="2">组别与关门时间</text:h>
   <text:p>十公里组：关门时间 3 小时，限报 800 人。</text:p>
   <text:p>二十一公里组：关门时间 6 小时，限报 500 人。</text:p>
   <text:p>四十二公里组：关门时间 9 小时，限报 300 人。</text:p>
   <text:h text:outline-level="2">报名与缴费</text:h>
   <text:p>报名截止时间：2026 年 10 月 15 日 18:00。</text:p>
   <text:p>咨询邮箱：race@example.com</text:p>
  </office:text>
 </office:body>
</office:document-content>
"""

ODP_CONTENT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" office:version="1.2">
 <office:body>
  <office:presentation>
   <draw:page draw:name="page1">
    <draw:frame>
     <draw:text-box>
      <text:p>秋季越野赛</text:p>
      <text:p>赛事筹备汇报 · 示例材料</text:p>
     </draw:text-box>
    </draw:frame>
   </draw:page>
   <draw:page draw:name="page2">
    <draw:frame>
     <draw:text-box>
      <text:p>本次比赛设三个组别</text:p>
      <text:p>十公里组：限报 800 人</text:p>
      <text:p>二十一公里组：限报 500 人</text:p>
     </draw:text-box>
    </draw:frame>
   </draw:page>
  </office:presentation>
 </office:body>
</office:document-content>
"""

# 结构化 ODT：一张带合并的表格 + 一个两级列表。
# 表格三列四行：A1:B1 横向合并、A2:A3 纵向合并，末行末尾是一个 `number-columns-repeated`
# 的空格补位（ODF 用它把一行补到固定列数，抽取时要照数落位才能保住列对应关系）。
ODT_STRUCTURED_CONTENT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" office:version="1.2">
 <office:body>
  <office:text>
   <text:h text:outline-level="1">赛事手册</text:h>
   <text:p>下面是一个两级列表。</text:p>
   <text:list>
    <text:list-item>
     <text:p>组别设置</text:p>
     <text:list>
      <text:list-item>
       <text:p>十公里组</text:p>
      </text:list-item>
      <text:list-item>
       <text:p>二十一公里组</text:p>
      </text:list-item>
     </text:list>
    </text:list-item>
    <text:list-item>
     <text:p>报名方式</text:p>
    </text:list-item>
   </text:list>
   <text:p>下面是一张带合并的表格。</text:p>
   <table:table table:name="组别表">
    <table:table-row>
     <table:table-cell table:number-columns-spanned="2" office:value-type="string"><text:p>合并标题</text:p></table:table-cell>
     <table:covered-table-cell/>
     <table:table-cell office:value-type="string"><text:p>c1</text:p></table:table-cell>
    </table:table-row>
    <table:table-row>
     <table:table-cell table:number-rows-spanned="2" office:value-type="string"><text:p>纵向合并</text:p></table:table-cell>
     <table:table-cell office:value-type="string"><text:p>b2</text:p></table:table-cell>
     <table:table-cell office:value-type="string"><text:p>c2</text:p></table:table-cell>
    </table:table-row>
    <table:table-row>
     <table:covered-table-cell/>
     <table:table-cell office:value-type="string"><text:p>b3</text:p></table:table-cell>
     <table:table-cell office:value-type="string"><text:p>c3</text:p></table:table-cell>
    </table:table-row>
    <table:table-row>
     <table:table-cell office:value-type="string"><text:p>d1</text:p></table:table-cell>
     <table:table-cell table:number-columns-repeated="3" office:value-type="string"><text:p></text:p></table:table-cell>
    </table:table-row>
   </table:table>
  </office:text>
 </office:body>
</office:document-content>
"""

def _load_font(size: int):
    """找一个可用的中文字体；都找不到时返回 None，由调用方退回英文内容。"""
    for candidate in CJK_FONT_CANDIDATES:
        path = Path(candidate)
        if path.exists():
            try:
                return ImageFont.truetype(str(path), size)
            except OSError:
                continue
    return None

def _page_lines(font) -> list:
    """按字体可用性选中文或英文正文。"""
    return PAGE_LINES_CJK if font is not None else PAGE_LINES_ASCII

def _add_hyperlink(paragraph, text: str, url: str) -> None:
    """
    往段落里追加一个**外部**超链接。

    功能：python-docx 没有公开的 add_hyperlink，所以按 OOXML 手工拼——建一条外部关系拿到
          rId，再把 `w:hyperlink`（带 r:id）挂到段落末尾。
    应用场景：阶段 16 造含超链接的 docx；测试要自己造这类文档时也可照这个写法。
    输入：paragraph — python-docx 段落对象；text — 链接文字（可为空）；url — 目标地址。
    输出：无。
    """
    from docx.opc.constants import RELATIONSHIP_TYPE as RT
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    rel_id = paragraph.part.relate_to(url, RT.HYPERLINK, is_external=True)
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), rel_id)
    run = OxmlElement("w:r")
    node = OxmlElement("w:t")
    node.text = text
    run.append(node)
    hyperlink.append(run)
    paragraph._p.append(hyperlink)

def _draw_page(lines: list, font) -> Image.Image:
    """把若干行文字画成一页白底图。"""
    image = Image.new("RGB", (PAGE_WIDTH, PAGE_HEIGHT), "white")
    draw = ImageDraw.Draw(image)
    y = PAGE_MARGIN
    for line in lines:
        if line:
            draw.text((PAGE_MARGIN, y), line, fill="black", font=font)
        y += int(FONT_SIZE * 1.7)
    return image

def step1_make_images() -> None:
    """造两张图：一张是「有文字的文档截图」，一张是「没有文字的风景照」。"""
    font = _load_font(FONT_SIZE)
    lines = _page_lines(font)
    screenshot = _draw_page(lines[:14], font)
    # 截图裁成文档比例，更像一张真实的屏幕截图
    screenshot = screenshot.crop((0, 0, PAGE_WIDTH, 900))
    screenshot.save(DATA_DIR / "screenshot.png")

    # 无文字图：用色块拼一个抽象风景，任何 OCR 都不该从里面读出文字
    photo = Image.new("RGB", (960, 640), (118, 176, 214))
    draw = ImageDraw.Draw(photo)
    draw.rectangle((0, 380, 960, 640), fill=(92, 138, 88))
    draw.ellipse((120, 90, 320, 290), fill=(246, 226, 150))
    draw.polygon([(520, 380), (700, 200), (880, 380)], fill=(150, 152, 156))
    photo.save(DATA_DIR / "photo.png")
    print(f"[阶段1] 已写入 {DATA_DIR / 'screenshot.png'}、{DATA_DIR / 'photo.png'}")

def step2_make_docx() -> None:
    """造带标题、段落、表格与内嵌图的 Word 文档。"""
    from docx import Document

    font = _load_font(FONT_SIZE)
    document = Document()
    document.add_heading("秋季越野赛竞赛规程", level=1)
    document.add_paragraph("本文档用于验证 DOCX 抽取：段落顺序、表格结构与内嵌图位置。")
    document.add_heading("组别与关门时间", level=2)

    table = document.add_table(rows=4, cols=3)
    table.style = "Table Grid"
    rows = [
        ("组别", "关门时间", "限报名额"),
        ("十公里", "3 小时", "800"),
        ("二十一公里", "6 小时", "500"),
        ("四十二公里", "9 小时", "300"),
    ]
    for row_index, row_values in enumerate(rows):
        for column_index, value in enumerate(row_values):
            table.cell(row_index, column_index).text = value

    document.add_paragraph("")
    document.add_paragraph("下面这张图是赛道路线示意，图上有文字说明。")
    document.add_picture(str(DATA_DIR / "screenshot.png"), width=None)

    document.add_heading("报名与缴费", level=2)
    document.add_paragraph("报名截止时间：2026 年 10 月 15 日 18:00。")
    document.add_paragraph("报名费：十公里 80 元，二十一公里 150 元，四十二公里 260 元。")
    document.add_paragraph("咨询邮箱：race@example.com")
    document.save(str(DATA_DIR / "report.docx"))
    print(f"[阶段2] 已写入 {DATA_DIR / 'report.docx'}（含表格与 1 张内嵌图）")

def step3_make_xlsx() -> None:
    """造单表 Excel，故意留空单元格以验证列对齐。"""
    import openpyxl

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "报名名单"
    sheet.append(["序号", "姓名", "组别", "报名费", "缴费状态", "备注"])
    groups = ["十公里", "二十一公里", "四十二公里"]
    for index in range(1, 41):
        group = groups[index % 3]
        fee = {"十公里": 80, "二十一公里": 150, "四十二公里": 260}[group]
        sheet.append(
            [
                index,
                f"选手{index:02d}",
                group,
                fee,
                "已缴费" if index % 4 else "",
                "需要发票" if index % 7 == 0 else "",
            ]
        )
    workbook.save(str(DATA_DIR / "inventory.xlsx"))
    print(f"[阶段3] 已写入 {DATA_DIR / 'inventory.xlsx'}（40 行，含空单元格）")

def step4_make_pptx() -> None:
    """造六页演示文稿：封面、要点页、表格页、图片页、收尾页。"""
    from pptx import Presentation
    from pptx.util import Inches

    presentation = Presentation()
    layout_blank = presentation.slide_layouts[6]

    slide = presentation.slides.add_slide(presentation.slide_layouts[0])
    slide.shapes.title.text = "秋季越野赛"
    slide.placeholders[1].text = "赛事筹备汇报 · 示例材料"

    slide = presentation.slides.add_slide(layout_blank)
    box = slide.shapes.add_textbox(Inches(0.8), Inches(0.6), Inches(8), Inches(4))
    box.text_frame.text = "本次比赛设三个组别"
    for line in ["十公里组：限报 800 人", "二十一公里组：限报 500 人", "四十二公里组：限报 300 人"]:
        box.text_frame.add_paragraph().text = line

    slide = presentation.slides.add_slide(layout_blank)
    table_shape = slide.shapes.add_table(4, 2, Inches(0.8), Inches(1.0), Inches(8), Inches(3))
    table = table_shape.table
    data = [
        ("组别", "关门时间"),
        ("十公里", "3 小时"),
        ("二十一公里", "6 小时"),
        ("四十二公里", "9 小时"),
    ]
    for row_index, row_values in enumerate(data):
        for column_index, value in enumerate(row_values):
            table.cell(row_index, column_index).text = value

    slide = presentation.slides.add_slide(layout_blank)
    slide.shapes.add_textbox(Inches(0.8), Inches(0.4), Inches(8), Inches(0.8)).text_frame.text = "赛道路线示意"
    slide.shapes.add_picture(str(DATA_DIR / "screenshot.png"), Inches(0.8), Inches(1.4), width=Inches(7))

    slide = presentation.slides.add_slide(layout_blank)
    slide.shapes.add_textbox(Inches(0.8), Inches(1.4), Inches(8), Inches(3)).text_frame.text = (
        "报名截止：2026 年 10 月 15 日 18:00\n咨询邮箱：race@example.com"
    )

    slide = presentation.slides.add_slide(layout_blank)
    slide.shapes.add_textbox(Inches(0.8), Inches(2.0), Inches(8), Inches(1.5)).text_frame.text = "谢谢"

    presentation.save(str(DATA_DIR / "deck.pptx"))
    print(f"[阶段4] 已写入 {DATA_DIR / 'deck.pptx'}（6 页，含表格与 1 张图）")

def step5_make_pdf() -> None:
    """造十页纯文本 PDF：每页一段规程，用于验证分页与阅读顺序。"""
    import pymupdf

    font = _load_font(FONT_SIZE)
    lines = _page_lines(font)
    document = pymupdf.open()
    for page_index in range(10):
        page = document.new_page(width=595, height=842)
        page.insert_text((60, 70), f"第 {page_index + 1} 页 / 共 10 页", fontname="china-s", fontsize=12)
        y = 110
        for line in lines:
            if line:
                # 用 PyMuPDF 内置中文字体，避免依赖系统字体注册
                page.insert_text((60, y), line, fontname="china-s", fontsize=11)
            y += 22
            if y > 800:
                break
    document.save(str(DATA_DIR / "manual.pdf"))
    document.close()
    print(f"[阶段5] 已写入 {DATA_DIR / 'manual.pdf'}（10 页，有文字层）")

def step6_make_scanned_pdf() -> None:
    """造三页扫描件：整页是图，没有任何文字层——用来验收扫描件的页面渲染补全（ADR 0006）。"""
    import pymupdf

    font = _load_font(FONT_SIZE)
    lines = _page_lines(font)
    document = pymupdf.open()
    for page_index in range(3):
        image_path = DATA_DIR / f"_page_{page_index + 1}.png"
        # 每页错开几行，页面内容互不相同
        page_image = _draw_page(lines[page_index : page_index + 18], font)
        page_image.save(image_path)
        page = document.new_page(width=595, height=842)
        page.insert_image(pymupdf.Rect(0, 0, 595, 842), filename=str(image_path))
        image_path.unlink()
    document.save(str(DATA_DIR / "scanned.pdf"))
    document.close()
    print(f"[阶段6] 已写入 {DATA_DIR / 'scanned.pdf'}（3 页，无文字层）")

def step7_make_archive() -> None:
    """造一个十几个条目的 ZIP，用于验证条目清单输出。"""
    target = DATA_DIR / "bundle.zip"
    entries = {
        "readme.txt": "示例压缩包，用于验证条目清单。\n",
        "data/config.json": '{"mode": "demo"}\n',
        "data/table.csv": "a,b\n1,2\n",
        "src/main.py": "print('hello')\n",
    }
    for index in range(1, 9):
        entries[f"docs/note_{index:02d}.txt"] = f"第 {index} 份说明。\n"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    print(f"[阶段7] 已写入 {target}（{len(entries)} 个条目）")

def step8_make_install_pkg() -> None:
    """造 APK 结构指纹样本：真正的结构特征是「ZIP 容器里有这几个文件」。"""
    target = DATA_DIR / "demo.apk"
    entries = {
        "AndroidManifest.xml": b"\x03\x00\x08\x00binary-manifest-placeholder",
        "classes.dex": b"dex\n035\x00placeholder",
        "resources.arsc": b"\x02\x00\x0c\x00placeholder",
        "lib/arm64-v8a/libdemo.so": b"\x7fELF placeholder",
        "res/layout/main.xml": b"<xml/>",
        "META-INF/MANIFEST.MF": b"Manifest-Version: 1.0\n",
    }
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    print(f"[阶段8] 已写入 {target}（APK 结构样本：{len(entries)} 个条目）")

def step9_make_legacy_office() -> None:
    """造老版 .doc 的头部样本，用于验证「缺依赖时降级为基础文件信息」这条路径。"""
    target = DATA_DIR / "legacy.doc"
    # OLE 复合文档魔数 + 填充，让识别走「老版 Office」分支
    target.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 512)
    print(f"[阶段9] 已写入 {target}（OLE 头样本，应触发降级而非报错）")

def step10_make_odf() -> None:
    """手工拼出最小可用的 odt 与 odp：ODF 就是 ZIP 里放 content.xml。"""
    for name, mimetype, content in (
        ("sample.odt", ODT_MIMETYPE, ODT_CONTENT_XML),
        ("sample.odp", ODP_MIMETYPE, ODP_CONTENT_XML),
    ):
        target = DATA_DIR / name
        with zipfile.ZipFile(target, "w") as archive:
            # ODF 规范要求 mimetype 是第一个条目且不压缩，否则部分读库会拒绝
            archive.writestr(zipfile.ZipInfo("mimetype"), mimetype, compress_type=zipfile.ZIP_STORED)
            archive.writestr("META-INF/manifest.xml", ODF_MANIFEST.replace("{mimetype}", mimetype))
            archive.writestr("content.xml", content)
            archive.writestr("styles.xml", ODF_STYLES_XML)
        print(f"[阶段10] 已写入 {target}")

def step11_make_audio() -> None:
    """造一段 3 秒 16kHz 单声道 wav，用于验证音频的结构信息与「送本机 ASR」这条链。"""
    target = DATA_DIR / "sample.wav"
    frames = bytearray()
    for index in range(AUDIO_SAMPLE_RATE * AUDIO_SECONDS):
        sample = int(AUDIO_AMPLITUDE * math.sin(2 * math.pi * AUDIO_TONE_HZ * index / AUDIO_SAMPLE_RATE))
        frames += struct.pack("<h", sample)
    with wave.open(str(target), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(AUDIO_SAMPLE_RATE)
        handle.writeframes(bytes(frames))
    print(f"[阶段11] 已写入 {target}（{AUDIO_SECONDS} 秒 16kHz 单声道，正弦音没有说话声）")

def step12_make_video() -> None:
    """
    把 sample.wav 封成一个 mp4，用于验证视频那条链（先抽音轨再转写）。

    ffmpeg 由 imageio-ffmpeg 提供（可选依赖）：没装就跳过并说明原因，
    此时视频这一类在服务里也是降级，样例与能力保持一致。
    视频轨只求「是个合法 mp4」，所以用 ffmpeg 内建的 mpeg4 编码器，不依赖外部编解码库。
    """
    source = DATA_DIR / "sample.wav"
    if not source.exists():
        print("[阶段12] 跳过：缺少 sample.wav，先跑阶段 11")
        return
    try:
        from imageio_ffmpeg import get_ffmpeg_exe
    except Exception as exc:  # pragma: no cover - 取决于部署环境
        print(f"[阶段12] 跳过：未安装 imageio-ffmpeg（{exc}）")
        return

    target = DATA_DIR / "sample.mp4"
    command = [
        get_ffmpeg_exe(), "-y",
        "-f", "lavfi", "-i", "color=c=black:s=320x240:r=10",
        "-i", str(source),
        "-shortest",
        "-c:v", "mpeg4", "-pix_fmt", "yuv420p", "-c:a", "aac",
        str(target),
    ]
    completed = subprocess.run(
        command,
        capture_output=True,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    if completed.returncode != 0 or not target.exists():
        detail = completed.stderr.decode("utf-8", "ignore").strip().splitlines()
        print(f"[阶段12] 失败：{detail[-1] if detail else completed.returncode}")
        return
    print(f"[阶段12] 已写入 {target}（3 秒视频，音轨取自 sample.wav）")

def step13_make_merged() -> None:
    """
    造三个含合并单元格的样例，用于验证表格格位建模。

    功能：docx 与 pptx 各一张 3x3 表（A1:B1 横向合并、A2:A3 纵向合并）；xlsx 两张表——
          第一张 4x3 同构（纵向跨 3 行），第二张只有 A1:C1 一处合并且覆盖列没有任何别的值。
    应用场景：验证「合并区文本只出现一次」与「覆盖位占列位、尾列不被裁掉」。
    输入：无（写 data/merged.docx、merged.pptx、merged.xlsx）。
    输出：无返回值；文件路径打印到 stdout。
    """
    from docx import Document
    from openpyxl import Workbook
    from pptx import Presentation
    from pptx.util import Inches

    body = (("", "", "c1"), ("", "b2", "c2"), ("", "b3", "c3"))

    document = Document()
    word_table = document.add_table(rows=3, cols=MERGED_COLUMNS)
    word_table.style = "Table Grid"
    for row_index, values in enumerate(body):
        for column_index, value in enumerate(values):
            word_table.cell(row_index, column_index).text = value
    # 先填后并：合并会把非左上格的文本并进左上格，期望值就不再是「只出现一次」
    word_table.cell(0, 0).merge(word_table.cell(0, 1)).text = MERGED_TITLE
    word_table.cell(1, 0).merge(word_table.cell(2, 0)).text = MERGED_VERTICAL
    document.save(str(DATA_DIR / "merged.docx"))
    print(f"[阶段13] 已写入 {DATA_DIR / 'merged.docx'}（3x3，横向与纵向合并各一处）")

    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    shape = slide.shapes.add_table(3, MERGED_COLUMNS, Inches(0.8), Inches(1.0), Inches(8), Inches(3))
    slide_table = shape.table
    for row_index, values in enumerate(body):
        for column_index, value in enumerate(values):
            slide_table.cell(row_index, column_index).text = value
    # python-pptx 的 merge() 不返回合并后的格（与 python-docx 不同），仍在左上格上写文本
    slide_table.cell(0, 0).merge(slide_table.cell(0, 1))
    slide_table.cell(0, 0).text = MERGED_TITLE
    slide_table.cell(1, 0).merge(slide_table.cell(2, 0))
    slide_table.cell(1, 0).text = MERGED_VERTICAL
    presentation.save(str(DATA_DIR / "merged.pptx"))
    print(f"[阶段13] 已写入 {DATA_DIR / 'merged.pptx'}（3x3，横向与纵向合并各一处）")

    workbook = Workbook()
    structured = workbook.active
    structured.title = "结构化合并"
    structured_body = (("", "", "c1"), ("", "b2", "c2"), ("", "b3", "c3"), ("", "b4", "c4"))
    for row_index, values in enumerate(structured_body, start=1):
        for column_index, value in enumerate(values, start=1):
            structured.cell(row=row_index, column=column_index, value=value)
    structured.merge_cells("A1:C1")
    structured["A1"] = MERGED_TITLE
    structured.merge_cells("A2:A4")
    structured["A2"] = MERGED_VERTICAL
    tail = workbook.create_sheet("尾部合并")
    tail.merge_cells("A1:C1")
    tail["A1"] = MERGED_TAIL
    workbook.save(str(DATA_DIR / "merged.xlsx"))
    print(f"[阶段13] 已写入 {DATA_DIR / 'merged.xlsx'}（4x3 结构合并 + 只有尾列合并的第二张表）")

def step14_make_repeated_images() -> None:
    """
    造三个「同一张图被引用多次」的样例，用于验证内嵌图按来源部件去重。

    功能：三个格式各造一份，photo.png 被引 `REPEATED_IMAGE_REPEATS` 次、screenshot.png 被引 1 次
          （共 4 处引用、2 张不同的图）。pptx 那份把重复引用分在同一页与跨页两处——
          python-pptx 的 rId 只在页内唯一、跨页复用同一个 image part，按 rId 去重会漏。
    应用场景：验证重复引用不占 `MAX_EMBEDDED_IMAGES` 名额、也不落内容重复的文件。
    输入：无（写 data/repeated.docx、repeated.pptx、repeated.pdf）。
    输出：无返回值；文件路径打印到 stdout。
    """
    import pymupdf
    from docx import Document
    from pptx import Presentation
    from pptx.util import Inches

    photo = DATA_DIR / "photo.png"
    screenshot = DATA_DIR / "screenshot.png"
    total_pages = REPEATED_IMAGE_REPEATS + 1

    document = Document()
    document.add_paragraph("下面同一张图被引用三次，随后是另一张图。")
    for _ in range(REPEATED_IMAGE_REPEATS):
        document.add_picture(str(photo))
    document.add_picture(str(screenshot))
    document.save(str(DATA_DIR / "repeated.docx"))
    print(f"[阶段14] 已写入 {DATA_DIR / 'repeated.docx'}（photo ×{REPEATED_IMAGE_REPEATS} + screenshot ×1）")

    presentation = Presentation()
    first_slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    first_slide.shapes.add_picture(str(photo), Inches(0.6), Inches(0.6), Inches(3), Inches(2))
    first_slide.shapes.add_picture(str(photo), Inches(4.2), Inches(0.6), Inches(3), Inches(2))
    second_slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    second_slide.shapes.add_picture(str(photo), Inches(0.6), Inches(0.6), Inches(3), Inches(2))
    third_slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    third_slide.shapes.add_picture(str(screenshot), Inches(0.6), Inches(0.6), Inches(3), Inches(2))
    presentation.save(str(DATA_DIR / "repeated.pptx"))
    print(f"[阶段14] 已写入 {DATA_DIR / 'repeated.pptx'}（第1页 photo ×2、第2页 photo ×1、第3页 screenshot ×1）")

    document = pymupdf.open()
    for page_index in range(REPEATED_IMAGE_REPEATS):
        page = document.new_page(width=595, height=842)
        # 必须有文字层：没有文字层的 PDF 会走整页渲染补全，根本不经过内嵌图那条路
        page.insert_text((60, 70), f"第 {page_index + 1} 页：同一张图在本页被引用一次", fontname="china-s", fontsize=12)
        page.insert_image(pymupdf.Rect(60, 110, 360, 310), filename=str(photo))
    last_page = document.new_page(width=595, height=842)
    last_page.insert_text((60, 70), f"第 {total_pages} 页：换另一张图", fontname="china-s", fontsize=12)
    last_page.insert_image(pymupdf.Rect(60, 110, 360, 310), filename=str(screenshot))
    # deflate 保存：PyMuPDF 默认把不透明 RGB 图按原始像素流存进 PDF（这 4 张就 5.0MB），
    # 压缩后 85KB；它只压流，不改 xref，所以「同一张图共用同一个 xref」这条前提不受影响
    document.save(str(DATA_DIR / "repeated.pdf"), deflate=True)
    document.close()
    print(f"[阶段14] 已写入 {DATA_DIR / 'repeated.pdf'}（前 {REPEATED_IMAGE_REPEATS} 页同一张图 + 第 {total_pages} 页另一张）")

def step15_make_structured_odt() -> None:
    """
    造一个带合并表格与两级列表的 odt，用于验证 ODT 按文档结构抽取。

    功能：写得与 sample.odt 同构（ZIP 里放 content.xml），但正文里有 `table:table` 与嵌套
          `text:list`——这两样正是全局 `root.iter()` 会拉平的结构。
    应用场景：验证表格按行列渲成制表符分隔、嵌套列表保住层级、合并区文本只出现一次。
    输入：无（写 data/structured.odt）。
    输出：无返回值；文件路径打印到 stdout。
    """
    target = DATA_DIR / "structured.odt"
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr(zipfile.ZipInfo("mimetype"), ODT_MIMETYPE, compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/manifest.xml", ODF_MANIFEST.replace("{mimetype}", ODT_MIMETYPE))
        archive.writestr("content.xml", ODT_STRUCTURED_CONTENT_XML)
        archive.writestr("styles.xml", ODF_STYLES_XML)
    print(f"[阶段15] 已写入 {target}（3 列 4 行合并表 + 两级列表）")

def step16_make_docx_hyperlinks() -> None:
    """
    造一个含超链接的 docx，用于验证「超链接的文字与地址都进交付文本」。

    功能：同一段落里放三条链接——地址 A 写两次（验证按地址去重）、再加一条没有文字的
          地址 B（验证「没有文字时只给 URL」）；去重后应剩 2 条。
    应用场景：验证 `docx._hyperlink_lines` 的抽取、去重与 `MAX_LINKS` 截断。
    输入：无（写 data/hyperlink.docx）。
    输出：无返回值；文件路径打印到 stdout。
    """
    from docx import Document

    document = Document()
    document.add_paragraph("下面三个超链接里，前两个指向同一个地址。")
    paragraph = document.add_paragraph()
    _add_hyperlink(paragraph, "示例站点", "https://example.com/a")
    _add_hyperlink(paragraph, "同一个地址", "https://example.com/a")
    _add_hyperlink(paragraph, "", "https://example.com/b")
    document.save(str(DATA_DIR / "hyperlink.docx"))
    print(f"[阶段16] 已写入 {DATA_DIR / 'hyperlink.docx'}（3 条链接、去重后 2 条）")

if __name__ == "__main__":
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # 阶段1：先造图片（后续文档与演示要内嵌它们）→ screenshot.png / photo.png
    step1_make_images()
    # 阶段2：Word 文档（表格 + 内嵌图）→ report.docx
    step2_make_docx()
    # 阶段3：Excel（40 行、含空单元格）→ inventory.xlsx
    step3_make_xlsx()
    # 阶段4：演示文稿（6 页）→ deck.pptx
    step4_make_pptx()
    # 阶段5：纯文本 PDF（10 页，有文字层）→ manual.pdf
    step5_make_pdf()
    # 阶段6：扫描件（3 页，无文字层）→ scanned.pdf
    step6_make_scanned_pdf()
    # 阶段7：压缩包 → bundle.zip
    step7_make_archive()
    # 阶段8：安装包结构样本 → demo.apk
    step8_make_install_pkg()
    # 阶段9：老版 Office 头样本 → legacy.doc
    step9_make_legacy_office()
    # 阶段10：ODF 文本文档与演示文稿 → sample.odt / sample.odp
    step10_make_odf()
    # 阶段11：音频样例（16kHz 单声道 wav）→ sample.wav
    step11_make_audio()
    # 阶段12：视频样例（由 sample.wav 封装，需要 imageio-ffmpeg）→ sample.mp4
    step12_make_video()
    # 阶段13：合并单元格样例（横向 / 纵向 / 尾部合并）→ merged.docx / merged.pptx / merged.xlsx
    step13_make_merged()
    # 阶段14：内嵌图重复引用样例（同一张图被引 3 次）→ repeated.docx / repeated.pptx / repeated.pdf
    step14_make_repeated_images()
    # 阶段15：结构化 ODT（带合并的表格 + 两级列表）→ structured.odt
    step15_make_structured_odt()
    # 阶段16：含超链接的 docx（同一地址写两次 + 一条没有文字的链接）→ hyperlink.docx
    step16_make_docx_hyperlinks()
    print(f"\n全部完成，输出目录：{DATA_DIR}")
