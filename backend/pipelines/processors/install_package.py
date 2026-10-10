# -*- coding: utf-8 -*-
"""
安装包处理器：apk / ipa / exe（全链路零模型，也不需要任何系统工具）。

与压缩包的区别在「读什么」：压缩包只列条目清单，安装包还要按特征文件判到具体的包类型
（Android APK / Flutter APK / iOS IPA）。文件头 + 条目清单 + 结构指纹三样一起报，
所以它单独成文件，不混进 archive.py。
"""
import zipfile
from pathlib import Path

import configs.limits as limits
from utils.textfile import read_header_hex
from pipelines.processors.archive import entries_to_content
from utils.processor_schema import ExtractResult

# APK / IPA 的结构指纹：命中哪些特征决定我们怎么描述它
APK_MARKERS = (
    "AndroidManifest.xml",
    "classes.dex",
    "resources.arsc",
)
FLUTTER_MARKER = "assets/flutter_assets/"

def extract_install_package(path: Path, ext: str) -> ExtractResult:
    """
    抽取安装包指纹：文件头 + 条目清单 + APK/IPA 结构特征。

    功能：APK/IPA 本质是 ZIP，先列目录再按特征文件判定包类型；
          .exe 只读 PE 头，不解析内部结构（那是另一个量级的工作）。
          条目名只保留上限内的前若干条写入 content；指纹扫描仍过一遍中央目录。
    应用场景：dispatch 分派 install_package 组时调用。
    输入：path — 文件路径；ext — 已识别的小写扩展名。
    输出：ExtractResult，content 为条目清单加指纹行。
    异常：不抛——解析失败时返回降级结果并说明原因。
    """
    header = read_header_hex(path)
    lines = [f"# {ext.upper()} 安装包", f"文件头: {header}"]

    if ext == "exe":
        lines.append("PE 可执行文件（MZ 头），只读取文件头，不解析内部结构")
        return ExtractResult(content="\n".join(lines), engines=["文件头识别"], entry_count=0)

    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
    except zipfile.BadZipFile as exc:
        return ExtractResult(
            content="\n".join(lines),
            engines=["文件头识别"],
            notes=[f"不是有效的 ZIP 容器: {exc}"],
            degraded=True,
            degrade_reason="安装包容器解析失败",
            degrade_code="install_package_parse_failed",
        )

    total = len(infos)
    entries = []
    apk_hits = []
    flutter = False
    ipa_plists = []
    # 清单只留上限内条目；指纹扫描仍过中央目录（特征文件可能在截断之后）
    for index, info in enumerate(infos):
        name = info.filename
        if index < limits.MAX_ENTRIES:
            entries.append(name)
        if name in APK_MARKERS:
            apk_hits.append(name)
        if name.startswith(FLUTTER_MARKER):
            flutter = True
        if name.startswith("Payload/") and name.endswith("Info.plist"):
            ipa_plists.append(name)

    if ext == "apk":
        if apk_hits:
            kind = "Flutter APK" if flutter else "Android APK"
            lines.append(f"识别为 {kind}，特征文件: {', '.join(apk_hits)}")
        else:
            lines.append("疑似 Android 安装包，但缺少 APK 特征文件")
    elif ext == "ipa":
        if ipa_plists:
            lines.append(f"识别为 iOS IPA，主 plist: {ipa_plists[0]}")
        else:
            lines.append("疑似 iOS 安装包，但缺少 Payload/*.app/Info.plist")

    content, count, hits = entries_to_content(entries, f"{ext.upper()} 容器", total=total)
    return ExtractResult(
        content="\n".join(lines) + "\n\n" + content,
        engines=["zipfile"],
        entry_count=count,
        limits_hit=hits,
    )
