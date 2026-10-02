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
    print(f"\n全部完成，输出目录：{DATA_DIR}")
