"""编码 / 输出语言 / 纯图片页 OCR 三项新功能的单元测试。"""

from __future__ import annotations

import json


from src.core.paths import Layout
from src.core.store import DocStore
from src.export.md_export import export_md_chapter
from src.export.render import labels_for, source_badges
from src.generate.prompts import language_instruction


def test_labels_for_languages():
    zh = labels_for("zh")
    en = labels_for("en")
    assert zh["types"]["definition"] == "定义"
    assert en["types"]["definition"] == "Definition"
    assert zh["relations"]["defines"] == "定义"
    assert en["relations"]["defines"] == "defines"
    assert "日本語" in language_instruction("ja")
    assert "English" in language_instruction("en")


def test_labels_drop_icons_for_non_utf8():
    assert labels_for("zh", encoding="utf-8")["icons"]
    assert labels_for("zh", encoding="gbk")["icons"] == {}


def test_export_encoding_gbk(store: DocStore, sample_doc, tmp_path):
    layout, doc_id = sample_doc
    anchor = store.anchor_rows("ch01")[1]
    store.create_block(
        {"type": "definition", "title": "定义", "content": "设 $A$ 为方阵",
         "sources": [{"anchor_id": anchor["anchor_id"], "quote": anchor["content"][:10],
                      "relation": "defines"}], "order": 10.0},
        author="ai", chapter_id="ch01")

    path = export_md_chapter(store, "ch01", out_dir=tmp_path / "gbk", lang="zh", encoding="gbk")
    raw = path.read_bytes()
    text = raw.decode("gbk")           # 必须能按 GBK 解码
    assert "定义" in text and "📖" not in text   # 非 UTF-8 编码去图标
    assert "（AI 归纳）" not in text

    path_en = export_md_chapter(store, "ch01", out_dir=tmp_path / "en", lang="en", encoding="utf-8")
    text_en = path_en.read_text(encoding="utf-8")
    assert "· defines]" in text_en          # 来源徽标标签随输出语言切换
    assert "· 定义]" not in text_en


def test_source_badges_language():
    block = {"sources": [{"anchor_id": "a_1", "quote": "q", "relation": "defines"}], "basis": "anchored"}
    anchors = {"a_1": {"page": 3}}
    assert "定义" in source_badges(block, anchors, mode="md")
    assert "defines" in source_badges(block, anchors, mode="md", labels=labels_for("en"))


def test_blank_page_triggers_ocr(sample_pdf, workspace: Layout, monkeypatch):
    """纯图片页：无文字层 → 按 ocr_mode 触发 OCR 并合入版面块（5.1.1）。"""
    from src.ingest import parse_pdf as pp

    calls: list[list[int]] = []

    class BlankAdapter(pp.PypdfAdapter):
        def parse_page(self, reader, page_index):
            meta = self._pdf_page_meta(reader.pages[page_index]) if hasattr(self, "_pdf_page_meta") else pp._pdf_page_meta(reader.pages[page_index])
            return {"status": "blank", "blocks": [], "meta": meta,
                    "source_space": "pdf_user_space", "source_to_pdf": [1, 0, 0, 1, 0, 0]}

    def fake_ocr(pdf_path, pages, pages_meta, **kwargs):
        calls.append(list(pages))
        vb = pages_meta[pages[0] - 1]["view_box"]
        return {"ok": True, "pages": [{"page": pages[0], "status": "ok", "blocks": [
            {"type": "paragraph", "level": 0, "text": "OCR 识别出的段落",
             "bbox": [vb[0] + 10, vb[1] + 10, vb[0] + 200, vb[1] + 40]}]}]}

    monkeypatch.setattr(pp, "get_adapter", lambda name, **kw: BlankAdapter())
    monkeypatch.setattr(pp, "ocr_pages_via_worker", fake_ocr)

    manifest = pp.parse_pdf(sample_pdf, workspace.ingest_root, "pypdf", layout=workspace,
                            doc_id="ocr-demo", title="OCR 示例",
                            config={"parser": {"ocr_mode": "auto"}})
    assert calls, "无文字层页面应触发 OCR"
    assert manifest["ocr"] is True

    page = json.loads((workspace.ingest_pages("ocr-demo") / "0001.json").read_text(encoding="utf-8"))
    texts = [b["text"] for b in page["blocks"]]
    assert any("OCR" in t for t in texts)


def test_ocr_disabled_by_config(sample_pdf, workspace: Layout, monkeypatch):
    from src.ingest import parse_pdf as pp

    calls: list = []

    class BlankAdapter(pp.PypdfAdapter):
        def parse_page(self, reader, page_index):
            meta = pp._pdf_page_meta(reader.pages[page_index])
            return {"status": "blank", "blocks": [], "meta": meta,
                    "source_space": "pdf_user_space", "source_to_pdf": [1, 0, 0, 1, 0, 0]}

    monkeypatch.setattr(pp, "get_adapter", lambda name, **kw: BlankAdapter())
    monkeypatch.setattr(pp, "ocr_pages_via_worker",
                        lambda *a, **k: calls.append(a) or {"ok": True, "pages": []})

    manifest = pp.parse_pdf(sample_pdf, workspace.ingest_root, "pypdf", layout=workspace,
                            doc_id="ocr-off", title="OCR 关闭",
                            config={"parser": {"ocr_mode": "off"}})
    assert not calls, "ocr_mode=off 时不得触发 OCR"
    assert manifest["ocr"] is False
