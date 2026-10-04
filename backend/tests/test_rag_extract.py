# -*- coding: utf-8 -*-
r"""
缝 R：RAG 抽取口（`POST /rag/extract/file`）。

缝：`pipelines/rag_extract.py` 的两条支路——PDF 印刷目录，与复用 extract 内核的交付文本。
它是本仓第二个契约（`routers/rag/schema.py`，ADR-0009），不进 ConvertResponse。

为什么直接调 `_try_catalogue()`：这个口只有上传入口，而上传要建运行期临时目录——沙箱里跑不了
（与 E1 同样的限制，所以 HTTP 那一路在 `test_extract_contract.py`）。这里只验「命中上界要跟着
响应报出去」这一条，喂假页数据即可，不写盘、不联网、不调模型。

预期值来源：`configs/limits.py` 的 `preview_first_pages`（TRUNCATE：目录只在前 N 页里找）
+ `docs/PIPELINES.md` 的 RAG 段。
"""
import pytest
from pathlib import Path

from routers.rag.schema import RagCatalogueRange, RagPage

# 能力域见 AGENTS.md「测试用例怎么写」；按域跑：python -m pytest tests -m capability_rag
pytestmark = pytest.mark.capability_rag


def _stub_pdf_pipeline(monkeypatch, page_count: int, preview_limit: int) -> None:
    """把「抽页 + 目录行 + 区间」三处换成假件，并钉住预览上限。"""
    from configs import limits
    from pipelines import rag_extract

    monkeypatch.setattr(limits, "PREVIEW_FIRST_PAGES", preview_limit)
    monkeypatch.setattr(
        rag_extract,
        "extract_pdf_pages",
        lambda path: [RagPage(page=index, text=f"第 {index} 页") for index in range(1, page_count + 1)],
    )
    monkeypatch.setattr(rag_extract, "extract_catalogue_lines", lambda preview: ["目录 1"])
    monkeypatch.setattr(
        rag_extract,
        "build_catalogue_ranges",
        lambda lines: [RagCatalogueRange(title="目录 1", start_page=1, end_page=2)],
    )


def test_catalogue_preview_limit_is_reported(monkeypatch):
    """
    行为：文档比预览上限长时，RAG 响应带 `limits_hit=["preview_first_pages"]`。

    来源：ADR-0003（能力边界要机器可读）+ `configs/limits.py` 的 `preview_first_pages`：
    目录只在前 N 页里找，文档更长就意味着**少读了用户的页**，不能只留在日志里。
    """
    _stub_pdf_pipeline(monkeypatch, page_count=5, preview_limit=2)

    from pipelines import rag_extract

    outcome = rag_extract.RagExtractPipeline()._try_catalogue(Path("fake.pdf"))

    assert outcome is not None
    assert outcome.status == 200
    assert outcome.response.limits_hit == ["preview_first_pages"]


def test_no_limit_is_reported_when_the_document_fits_the_preview(monkeypatch):
    """行为：文档没超过预览上限时不谎报命中（反向断言，防「恒报」）。来源：同上。"""
    _stub_pdf_pipeline(monkeypatch, page_count=1, preview_limit=5)

    from pipelines import rag_extract

    outcome = rag_extract.RagExtractPipeline()._try_catalogue(Path("fake.pdf"))

    assert outcome is not None
    assert outcome.response.limits_hit == []
