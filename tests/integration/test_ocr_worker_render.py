"""OCR worker 回归：render_dir 存渲染页不得破坏 OCR 图像（曾整本书全变空页）。"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from src.ingest.parse_pdf import _pdf_page_meta, ocr_pages_via_worker, resolve_worker_python

pytestmark = pytest.mark.skipif(
    not Path(resolve_worker_python() or "").exists(),
    reason="需要 .venv-parsers（OCR worker 环境）")


def test_ocr_worker_with_render_dir_still_recognizes(workspace, sample_doc):
    """bitmap.save 会破坏 pdfium 位图：带 render_dir 时 OCR 曾返回 0 块——
    存 PIL 副本后两者必须兼得（块 > 0 且渲染页落盘）。"""
    layout, doc_id = sample_doc
    pdf = layout.textbooks_dir / f"{doc_id}.pdf"
    if not pdf.exists():
        src = layout.root / "sample.pdf"
        if not src.exists():
            pytest.skip("sample pdf 不在工作区")
        shutil.copy(src, pdf)
    import pypdf

    reader = pypdf.PdfReader(str(pdf))
    metas = [_pdf_page_meta(p) for p in reader.pages]
    render_dir = layout.ingest_root / doc_id / "_ocr_png_test"
    out = ocr_pages_via_worker(pdf, [1], metas, python=resolve_worker_python(),
                               render_dir=str(render_dir))
    assert out is not None
    page = (out.get("pages") or [{}])[0]
    assert len(page.get("blocks") or []) > 0, \
        f"OCR 有 render_dir 时必须照常识别（曾 0 块）: {page}"
    assert list(render_dir.glob("*.png")), "渲染页必须落盘（视觉补 OCR 的输入）"
