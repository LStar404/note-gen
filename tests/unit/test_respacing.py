"""参考文本对齐重排回归：内容一字不改，只修排版伪影。"""

from __future__ import annotations

from src.core.text import respaces_from_reference, squash_key
from src.ingest.parse_pdf import _spacing_garbage_runs, _text_quality_ok

BS = chr(92)


def test_respaces_garbled_run():
    old = "The s ou s i n g of the theorem"
    ref = "The so using of the theorem"
    out = respaces_from_reference(old, ref)
    assert out == ref
    assert squash_key(out)[0] == squash_key(old)[0], "内容指纹必须一字不差"


def test_respaces_keeps_differing_glyph_regions():
    """两个解析器对同一字形给出不同残留（∫∫ vs \x0f\x0f）→ 差异段保留原文。"""
    old = "Let f( x,y) ∫∫ g(x) end"
    ref = "Let f (x,y) " + chr(0x0F) * 2 + " g (x) end"
    out = respaces_from_reference(old, ref)
    assert "∫∫" in out, "字形差异段不得被垃圾字形覆盖"
    assert out == "Let f (x,y) ∫∫ g (x) end"


def test_respaces_idempotent_and_noop():
    s = "Plain text without artifacts"
    assert respaces_from_reference(s, s) == s
    old = "a s s i m i l a t e here"
    ref = "a ssimilate here"
    once = respaces_from_reference(old, ref)
    assert respaces_from_reference(once, ref) == once


def test_spacing_garbage_detector():
    assert _spacing_garbage_runs("T h e o r e m 1.1") == 1
    assert _spacing_garbage_runs("Normal English text with a b tokens") == 0
    assert _spacing_garbage_runs("s ou s i n gT h e o r e m") >= 1


class _Page:
    def __init__(self, text):
        self._t = text

    def extract_text(self):
        return self._t


class _Reader:
    def __init__(self, texts):
        self.pages = [_Page(t) for t in texts]


def test_quality_probe_rejects_spaced_letter_garbage():
    clean = ["Theorem 1.1. Let X be a random variable. " * 8] * 3
    assert _text_quality_ok(_Reader(clean)) is True
    garbled = ["T h e o r e m 1 . 1 . L e t X b e " * 8] * 3
    assert _text_quality_ok(_Reader(garbled)) is False
