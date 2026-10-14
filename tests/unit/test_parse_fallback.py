"""解析器回退链回归：构造成功 ≠ 转换成功（marker 装着但要 Docker，转换期才爆）。
图片 PDF 曾直接解析失败、OCR 兜底没机会跑。"""

from __future__ import annotations

import sys
import types

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


def _empty_ok_adapter(name: str, n_pages: int = 2):
    """「成功但零文本」的转换器（实测 docling 对纯图片书如此）——不得算成功。"""

    class Fake:
        def __init__(self, python=None):
            self.name = name
            self.version = "0"

        def parse_document(self, pdf_path, pages_meta=None):
            return {"ok": True, "warnings": [], "coordinate": {},
                    "pages": [{"status": "blank", "blocks": []} for _ in range(n_pages)]}

    return Fake


def _fake_pypdf(monkeypatch, pp, blocks: list):
    """把非 bulk 的 pypdf 适配器换成受控实现（2 页样例走顺序路径，不触发进程池）。"""
    class Fake:
        def __init__(self, python=None):
            self.name = "pypdf"
            self.version = "0"

        def parse_page(self, reader, i):
            return {"status": "ok" if blocks else "blank", "blocks": list(blocks)}

    monkeypatch.setattr(pp, "PypdfAdapter", Fake)


def test_zero_text_converter_is_not_success(sample_doc, monkeypatch, capsys):
    """「ok=True + 全空页」不算转换成功：链继续试下一个（曾静默导入成整本空页）。"""
    import src.ingest.parse_pdf as pp

    layout, doc_id = sample_doc
    pdf = layout.textbooks_dir / "sample-la.pdf"
    if not pdf.exists():
        import shutil

        src_pdf = layout.root / "sample.pdf"
        if not src_pdf.exists():
            pytest.skip("sample pdf 不在工作区")
        shutil.copy(src_pdf, pdf)
    monkeypatch.setattr(pp, "_has_text_layer", lambda reader: False)
    monkeypatch.setattr(pp, "MarkerAdapter", _empty_ok_adapter("marker"))
    monkeypatch.setattr(pp, "DoclingAdapter", _empty_ok_adapter("docling"))
    monkeypatch.setattr(pp, "PyMupdfAdapter", _unavailable_adapter("pymupdf"))
    _fake_pypdf(monkeypatch, pp, [{"type": "paragraph", "level": 0, "text": "兜底文本",
                                   "bbox": [10.0, 10.0, 100.0, 40.0]}])
    man = pp.parse_pdf(pdf, layout.ingest_root, "auto", layout=layout,
                       doc_id="zero-la", title="t")
    assert man["chapters"]
    err = capsys.readouterr().err
    assert "未识别到文本" in err, err


def test_all_zero_blocks_fails_loudly(sample_doc, monkeypatch):
    """全链零文本必须响亮报错（任务不许显示「完成」却产不出锚点）。"""
    import src.ingest.parse_pdf as pp

    layout, doc_id = sample_doc
    pdf = layout.textbooks_dir / "sample-la.pdf"
    if not pdf.exists():
        import shutil

        src_pdf = layout.root / "sample.pdf"
        if not src_pdf.exists():
            pytest.skip("sample pdf 不在工作区")
        shutil.copy(src_pdf, pdf)
    monkeypatch.setattr(pp, "_has_text_layer", lambda reader: False)
    monkeypatch.setattr(pp, "MarkerAdapter", _empty_ok_adapter("marker"))
    monkeypatch.setattr(pp, "DoclingAdapter", _empty_ok_adapter("docling"))
    monkeypatch.setattr(pp, "PyMupdfAdapter", _unavailable_adapter("pymupdf"))
    _fake_pypdf(monkeypatch, pp, [])   # 兜底也不出文本
    with pytest.raises(PipelineError) as ei:
        # ocr_mode=off：关掉 OCR 兜底才测得到「全零块」这条闸
        pp.parse_pdf(pdf, layout.ingest_root, "auto", layout=layout,
                     doc_id="zero2-la", title="t",
                     config={"parser": {"ocr_mode": "off"}})
    assert "未识别到任何文本" in str(ei.value), ei.value


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


def _fake_docling(monkeypatch, page_height: float, items: list):
    class Cvt:
        def convert(self, path):
            doc = types.SimpleNamespace(
                pages={1: types.SimpleNamespace(size=types.SimpleNamespace(height=page_height))})
            doc.iterate_items = lambda: iter([(it, 0) for it in items])
            return types.SimpleNamespace(document=doc)

    mod = types.ModuleType("docling.document_converter")
    mod.DocumentConverter = Cvt
    monkeypatch.setitem(sys.modules, "docling", types.ModuleType("docling"))
    monkeypatch.setitem(sys.modules, "docling.document_converter", mod)


def test_docling_emit_scales_page_units(monkeypatch):
    """docling 页高单位未必是点（实测像素单位 y=1568 曾在 880pt 页上越界炸掉整本书）：
    按页高关系等比映射 + 夹进 view_box。"""
    from src.ingest import parser_workers as pw

    item = types.SimpleNamespace(
        label="text", text="行内容",
        prov=[types.SimpleNamespace(page_no=1, bbox=[313.5, 382.0, 347.8, 403.0])])
    _fake_docling(monkeypatch, 1760.0, [item])
    res = pw.docling_extract({"pdf_path": "x.pdf",
                              "pages_meta": [{"view_box": [0.0, 0.0, 612.0, 880.0],
                                              "user_unit": 1.0, "rotation": 0}]})
    blocks = res["pages"][0]["blocks"]
    assert len(blocks) == 1, res
    x0, y0, x1, y1 = blocks[0]["bbox"]
    # 1760px 页高 → 880pt：等比 0.5，y 翻转：[156.75, 678.5, 173.9, 689.0]
    assert abs(x0 - 156.75) < 0.01 and abs(x1 - 173.9) < 0.01, blocks[0]["bbox"]
    assert abs(y0 - 678.5) < 0.01 and abs(y1 - 689.0) < 0.01, blocks[0]["bbox"]


def test_docling_emit_drops_fully_outside_block(monkeypatch):
    """整块越界：丢弃并计警告，不猜位置、不报错。"""
    from src.ingest import parser_workers as pw

    item = types.SimpleNamespace(
        label="text", text="越界块",
        prov=[types.SimpleNamespace(page_no=1, bbox=[2000.0, 2000.0, 2100.0, 2100.0])])
    _fake_docling(monkeypatch, 1760.0, [item])
    res = pw.docling_extract({"pdf_path": "x.pdf",
                              "pages_meta": [{"view_box": [0.0, 0.0, 612.0, 880.0],
                                              "user_unit": 1.0, "rotation": 0}]})
    assert res["pages"][0]["blocks"] == [], res["pages"][0]
    assert any("丢弃" in w for w in res.get("warnings", [])), res.get("warnings")


def test_driver_skips_bad_block_instead_of_aborting(sample_doc, monkeypatch, capsys):
    """单块坐标非法不得炸整本书（实测一个坏块毁掉 230/364 页的导入）：跳过并记录。"""
    import shutil

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
            pages = [{"status": "ok", "blocks": [
                {"type": "paragraph", "level": 0, "text": "好块",
                 "bbox": [10.0, 10.0, 100.0, 50.0]},
                {"type": "paragraph", "level": 0, "text": "坏块",
                 "bbox": [10.0, 5000.0, 100.0, 5100.0]},
            ]} for _ in pages_meta]
            return {"ok": True, "pages": pages,
                    "coordinate": {"source_space": "pdf_user_space",
                                   "source_to_pdf": [1, 0, 0, 1, 0, 0], "derivation": ""},
                    "warnings": []}

    monkeypatch.setattr(pp, "get_adapter", lambda name, python=None: FakeBulk())
    man = pp.parse_pdf(pdf, layout.ingest_root, "fake", layout=layout,
                       doc_id="skipbad-la", title="t")
    assert man["chapters"], "坏块跳过后导入必须完成"
    page1 = (layout.ingest_root / "skipbad-la" / "pages" / "0001.json").read_text(encoding="utf-8")
    assert "好块" in page1 and "坏块" not in page1, page1[:400]
    err = capsys.readouterr().err
    assert "坐标非法已跳过" in err, err
