"""中文 PDF 抽取保真回归：缺 ToUnicode 乱码必须走 pymupdf，渲染资源必备。"""

from __future__ import annotations

import importlib.util

import pytest

from src.ingest.parse_pdf import _text_quality_ok
from src.ingest.parser_workers import _fitz_line_type


class _FakePage:
    def __init__(self, text: str):
        self._t = text

    def extract_text(self):
        return self._t


class _FakeReader:
    def __init__(self, text: str):
        self.pages = [_FakePage(text)] * 3


def test_quality_probe_rejects_garbled_cjk():
    """缺 ToUnicode 的中文 PDF 抽成孟加拉/格鲁吉亚乱码 → 质量探测必须判 False。"""
    # 正文页的真实乱码形态：汉字整段变成孟加拉/格鲁吉亚/藏文码位（实测样本）
    garbled = "\u09e8\u09b0\u09cd\u09b7\u09c0\u09c7 \u09a8\u09cd\u09a4\u09bf \u09b0\u09c7\u09b7\u09be\u09cd\u099a"               "\u10db\u10d0\u10d7\u10d4\u10db\u10d0\u10d7\u10d8\u0f00\u0f01\u0f02\u09d8\u09d9\u09da"
    assert _text_quality_ok(_FakeReader(garbled)) is False


def test_quality_probe_accepts_clean_text():
    clean = "第三章 全纯函数的级数展开。Theorem 3.1. 设 f 在 D 内全纯，$f(z)=\\sum a_n z^n$。"
    assert _text_quality_ok(_FakeReader(clean)) is True


def test_fitz_line_type_heuristics():
    assert _fitz_line_type("第 3 章 全纯开拓", 16.0, 10.0)[0] == "heading"
    assert _fitz_line_type("u^3 + v^3 + b = 0,", 10.0, 10.0)[0] == "formula"
    assert _fitz_line_type("设 f 在区域 D 内全纯。", 10.0, 10.0)[0] == "paragraph"


@pytest.mark.skipif(importlib.util.find_spec("fitz") is None
                    and importlib.util.find_spec("pymupdf") is None,
                    reason="pymupdf 不在当前解释器（worker 环境专属）")
def test_pymupdf_worker_extracts_text(workspace, tmp_path):
    from src.ingest.parser_workers import pymupdf_extract
    from tests.fixtures.make_sample_pdf import build_sample_pdf

    pdf = build_sample_pdf(tmp_path / "s.pdf")
    out = pymupdf_extract({"pdf_path": str(pdf),
                           "pages_meta": [{"view_box": [0, 0, 595, 842],
                                           "user_unit": 1.0, "rotation": 0}]})
    assert out["ok"] is True
    assert out["coordinate"]["source_space"] == "pdf_user_space"
    assert out["pages"][0]["blocks"], "必须抽出文本行"
