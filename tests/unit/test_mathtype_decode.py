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


def test_relation_family_not_vertical_ellipsis():
    """SYAT/SYAS 的 six 是 <=（楔形+横杠），历史误映射成 ⋮ 波及 479 处。"""
    assert decode_glyph("MT2SYAT", "six", 54) == "\u2264"
    assert decode_glyph("MT2SYAS", "six", 54) == "\u2264"
    assert decode_glyph("MT2SYAT", "greater", 62) == "\u2265"


def test_negation_slash_combines():
    """MT2SYT.six 是否定斜杠：与后随符号合成 ≢ / ∉。"""
    from src.ingest.mathtype_decode import combine_negations
    run = decode_glyph("MT2SYT", "six", 54) + decode_glyph("MT2SYT", "DC1", 0)
    assert combine_negations(run) == "\u2262"          # ≢
    run2 = decode_glyph("MT2SYT", "six", 54) + decode_glyph("MT2SYT", "two", 50)
    assert combine_negations(run2) == "\u2209"         # ∉


def test_bigop_fragments():
    assert decode_glyph("MT2EXA", "backslash", 92) == "\u22c2"   # ⋂
    assert decode_glyph("MT2EXA", "bracketleft", 91) == "\u22c3"  # ⋃


def test_blackboard_and_unknown_fallback():
    assert decode_glyph("MSBM10", "R", 82) == "\u211d"
    assert decode_glyph("MSBM10", "C", 67) == "\u2102"
    assert decode_glyph("FZSSK--GBK1-0", "?", 0x4E2D) == "中"   # 非数学字体原样
    assert decode_glyph("MT2SYT", "UNKNOWN_CODE", 88) == "X"    # 未知编码回退抽取值
