"""MathType MT2 字体解码回归：私有编码字形名错位的恢复。"""

from __future__ import annotations

from src.ingest.mathtype_decode import combine_negations, decode_glyph, decode_mt2_text


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


def test_decode_mt2_text_from_extractor_chars():
    """抽取器给的原始文本（字形名→AGL 字符）整段还原。"""
    # MT2SYT: two→∈、D→=、six→否定斜杠；MT2MIT: period→(、slash→)、acute→ζ
    assert decode_mt2_text("MT2SYT", "a2b") == "a\u2208b"
    assert decode_mt2_text("ABCDEF+MT2MIT", "f.\u00b4/") == "f(\u03b6)"
    assert decode_mt2_text("MT2SYT", "1D2") == "\u221e=\u2208"   # one→∞、D→=、two→∈
    # 否定斜杠组合：six+DC1 → ≢（跨字形合成）
    assert decode_mt2_text("MT2SYT", "x6" + chr(0x11) + "y") == "x\u2262y"
    # 非数学字体原样
    assert decode_mt2_text("FZSSK--GBK1-0", "中文 a2b") == "中文 a2b"
    # U+FFFD（控制名字形占位）丢弃
    assert decode_mt2_text("MT2SYT", "a\ufffdb") == "ab"


def test_decode_mt2_text_combine_flag():
    """combine=False 不做否定合成，供跨 span 拼接后统一处理。"""
    parts = decode_mt2_text("MT2SYT", "6", combine=False)
    parts += decode_mt2_text("MT2SYT", chr(0x11), combine=False)
    assert combine_negations(parts) == "\u2262"


def test_bigop_fragment_glyphs_dropped():
    """MT2EXA 未登记字形是定界符分片：丢弃占位字符，不留垃圾。"""
    assert decode_glyph("MT2EXA", "acute", 180) == ""
    assert decode_glyph("MT2EXA", "at", 64) == ""
    assert decode_glyph("ABCDEF+MT2EXA", "zero", 48) == ""
    # 已登记大算符保留
    assert decode_glyph("MT2EXA", "backslash", 92) == "\u22c2"


def test_decode_font_text_uses_basefont():
    """pypdf 适配器按 /BaseFont 解码（带子集前缀）。"""
    from src.ingest.parse_pdf import _decode_font_text

    fd = {"/BaseFont": "/ABCDEF+MT2SYT"}
    assert _decode_font_text(fd, "a2b") == "a\u2208b"
    # 非 MT2 字体与缺 font_dict 均原样
    assert _decode_font_text({"/BaseFont": "/XYZ+FZSSK"}, "中文") == "中文"
    assert _decode_font_text(None, "raw") == "raw"


def test_blackboard_and_unknown_fallback():
    assert decode_glyph("MSBM10", "R", 82) == "\u211d"
    assert decode_glyph("MSBM10", "C", 67) == "\u2102"
    assert decode_glyph("FZSSK--GBK1-0", "?", 0x4E2D) == "中"   # 非数学字体原样
    assert decode_glyph("MT2SYT", "UNKNOWN_CODE", 88) == "X"    # 未知编码回退抽取值
