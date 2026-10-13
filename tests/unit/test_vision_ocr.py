"""视觉补 OCR 回归：弱识别判定、几何映射、解析管线挂钩。"""

from __future__ import annotations

import json
import shutil

import pytest

from src.core.errors import InputError
from src.ingest.vision_ocr import fracs_to_bbox, weak_ocr_text


def test_weak_ocr_heuristic():
    assert weak_ocr_text("")
    assert weak_ocr_text("a\ufffdb c")            # 替换符=乱码
    assert weak_ocr_text("#%&*  $\\{  ~^|!")     # 符号密度过高
    assert not weak_ocr_text("设函数 f 在区域 D 内全纯，则它的积分表示与留数定理密切相关，收敛半径为 R。")


def test_fracs_to_bbox_flips_y():
    x0, y0, x1, y1 = fracs_to_bbox([0.0, 0.0, 1.0, 0.25], [0.0, 0.0, 612.0, 880.0])
    assert (x0, x1) == (0.0, 612.0)
    assert abs(y1 - 880.0) < 1e-6 and abs(y0 - 660.0) < 1e-6   # 页顶 25% → y-up 高段


def test_parse_pdf_uses_vision_for_weak_pages(sample_doc, monkeypatch, capsys):
    """本地 OCR 乱码页 → 视觉重识别；视觉失败保留本地结果不炸导入。"""
    import src.ingest.parse_pdf as pp

    layout, doc_id = sample_doc
    pdf = layout.textbooks_dir / "sample-la.pdf"
    if not pdf.exists():
        src_pdf = layout.root / "sample.pdf"
        if not src_pdf.exists():
            pytest.skip("sample pdf 不在工作区")
        shutil.copy(src_pdf, pdf)

    class FakeBulk:
        name = "fake"
        version = "1"

        def parse_document(self, pdf_path, pages_meta=None):
            return {"ok": True, "pages": [{"status": "blank", "blocks": []} for _ in pages_meta],
                    "coordinate": {"source_space": "pdf_user_space",
                                   "source_to_pdf": [1, 0, 0, 1, 0, 0], "derivation": ""},
                    "warnings": []}

    monkeypatch.setattr(pp, "get_adapter", lambda name, python=None: FakeBulk())
    monkeypatch.setattr(pp, "_has_text_layer", lambda reader: False)
    monkeypatch.setattr(pp, "MarkerAdapter", type("M", (), {"__init__": lambda self, python=None: (_ for _ in ()).throw(InputError("no marker"))}))
    monkeypatch.setattr(pp, "DoclingAdapter", type("D", (), {"__init__": lambda self, python=None: (_ for _ in ()).throw(InputError("no docling"))}))

    # OCR worker：返回乱码弱结果 + 落一个假 PNG（视觉输入）
    def fake_ocr(pdf_path, pages, pages_meta, **kw):
        rd = kw.get("render_dir")
        if rd:
            from pathlib import Path
            Path(rd).mkdir(parents=True, exist_ok=True)
            for pg in pages:
                (Path(rd) / f"page_{pg:04d}.png").write_bytes(b"png")
        return {"ok": True, "parser": "ocr", "version": "t", "pages": [
            {"page": pg, "status": "ok", "blocks": [
                {"type": "paragraph", "level": 0, "text": "#%&* ~^", "bbox": [10.0, 10.0, 100.0, 40.0],
                 "confidence": None}]} for pg in pages]}

    monkeypatch.setattr(pp, "ocr_pages_via_worker", fake_ocr)
    vision_calls = []

    def fake_vision(png_path):
        vision_calls.append(png_path)
        return [{"type": "paragraph", "level": 0, "text": "视觉识别的正文内容",
                 "frac": [0.05, 0.1, 0.95, 0.2]}]

    man = pp.parse_pdf(pdf, layout.ingest_root, "fake", layout=layout,
                       doc_id="vision-la", title="t", vision_ocr=fake_vision)
    assert man["chapters"], man
    assert vision_calls, "弱识别页必须触发视觉补 OCR"
    page1 = json.loads((layout.ingest_root / "vision-la" / "pages" / "0001.json").read_text(encoding="utf-8"))
    texts = [b.get("text") for b in page1.get("blocks", [])]
    assert any("视觉识别" in (t or "") for t in texts), texts
    assert not (layout.ingest_root / "vision-la" / "_ocr_png").exists(), "渲染中间产物必须清理"
