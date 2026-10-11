"""MathType MT2 字体解码回归：私有编码字形名错位的恢复。"""

from __future__ import annotations

from src.ingest.mathtype_decode import decode_glyph


def test_symbol_family_decoding():
    assert decode_glyph("MT2SYT", "D", 68) == "="
    assert decode_glyph("MT2SYT", "NUL", 0) == "\u2212"
    assert decode_glyph("MT2SYT", "two", 50) == "\u2208"
    assert decode_glyph("MT2SYS", "C", 67) == "+"      # 同族共享表
    assert decode_glyph("MT2SYT", "onesuperior", 185) == "{"


def test_italic_family_decoding():
    assert decode_glyph("MT2MIT", "period", 46) == "("
    assert decode_glyph("MT2MIT", "slash", 47) == ")"
    assert decode_glyph("MT2MIT", "acute", 180) == "\u03b6"
    assert decode_glyph("MT2MIS", "semicolon", 59) == ","    # 同族共享表
    assert decode_glyph("MT2MIT", "a", 97) == "a"            # 字母原样


def test_blackboard_and_unknown_fallback():
    assert decode_glyph("MSBM10", "R", 82) == "\u211d"
    assert decode_glyph("MSBM10", "C", 67) == "\u2102"
    assert decode_glyph("FZSSK--GBK1-0", "?", 0x4E2D) == "中"   # 非数学字体原样
    assert decode_glyph("MT2SYT", "UNKNOWN_CODE", 88) == "X"    # 未知编码回退抽取值
