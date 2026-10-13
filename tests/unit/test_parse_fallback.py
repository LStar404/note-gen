"""解析器回退链回归：构造成功 ≠ 转换成功（marker 装着但要 Docker，转换期才爆）。
图片 PDF 曾直接解析失败、OCR 兜底没机会跑。"""

from __future__ import annotations

import pytest

from src.core.errors import InputError, PipelineError


def _failing_adapter(name: str, msg: str):
    class Fake:
        def __init__(self, python=None):
            self.name = name
            self.version = "0"

        def parse_document(self, pdf_path, pages_meta=None):
            raise PipelineError(msg)

    return Fake


def _unavailable_adapter(name: str):
    class Fake:
        def __init__(self, python=None):
            raise InputError(f"{name} 不可用")

    return Fake


def test_scan_route_falls_back_through_chain(sample_doc, monkeypatch, capsys):
    """扫描书路由：marker/docling 转换失败 → pymupdf 不可用 → pypdf 兜底成功，不硬失败。"""
    import shutil

    import src.ingest.parse_pdf as pp

    layout, doc_id = sample_doc
    pdf = layout.textbooks_dir / "sample-la.pdf"
    if not pdf.exists():
        src_pdf = layout.root / "sample.pdf"
        if not src_pdf.exists():
            pytest.skip("sample pdf 不在工作区")
        shutil.copy(src_pdf, pdf)
    monkeypatch.setattr(pp, "_has_text_layer", lambda reader: False)
    monkeypatch.setattr(pp, "MarkerAdapter", _failing_adapter("marker", "docker binary not found"))
    monkeypatch.setattr(pp, "DoclingAdapter", _failing_adapter("docling", "转换失败"))
    monkeypatch.setattr(pp, "PyMupdfAdapter", _unavailable_adapter("pymupdf"))
    man = pp.parse_pdf(pdf, layout.ingest_root, "auto", layout=layout,
                       doc_id="fallback-la", title="t")
    assert man["chapters"], "回退到 pypdf 也必须产出章节"
    err = capsys.readouterr().err
    assert err.count("回退") >= 3, err
    assert "docker binary not found" in err, err


def test_explicit_parser_does_not_fall_back(sample_doc, monkeypatch):
    """显式指定解析器不回退：用户点名即意图，失败要报清楚而不是悄悄换引擎。"""
    import shutil

    import src.ingest.parse_pdf as pp

    layout, doc_id = sample_doc
    pdf = layout.textbooks_dir / "sample-la.pdf"
    if not pdf.exists():
        src_pdf = layout.root / "sample.pdf"
        if not src_pdf.exists():
            pytest.skip("sample pdf 不在工作区")
        shutil.copy(src_pdf, pdf)
    monkeypatch.setattr(pp, "MarkerAdapter", _failing_adapter("marker", "docker binary not found"))
    with pytest.raises(PipelineError) as ei:
        pp.parse_pdf(pdf, layout.ingest_root, "marker", layout=layout,
                     doc_id="explicit-la", title="t")
    assert "docker" in str(ei.value), str(ei.value)
