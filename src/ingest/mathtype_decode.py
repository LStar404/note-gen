"""MathType MT2 字体解码：私有编码 + 字形名错位的 PDF 文本恢复。

MathType 导出的 PDF 用 MT2 系列 Type1 字体排数学符号，字体无 ToUnicode、
字形名也只是「编码位的 AGL 名」（名为 period 的字形画的是括号）——pypdf 与
pymupdf 都会把公式抽出成 ".a;b/ D.ac" 这类乱码（实测史济怀《复变函数》）。

本表由「页面渲染真值 + 字形对照图」逐字建立（见 2026-10-11 修复记录）：
按 (字体族, 编码名) → 真实符号 解码。字体族内的编码表一致：
- MT2SYT/SYS/SYF/SYAT/SYAS：数学符号（D→=、NUL→−、two→∈ …）
- MT2MIT/MIS/MIF/BMIT：斜体字母数字（原样）+ 希腊字母（acute→ζ …）
- MT2EXA：大定界符分片（本表仅收录已确认项）
- MSBM10：黑板体字母（C→ℂ、R→ℝ …）
"""

from __future__ import annotations

# 数学符号族（MT2SYT 系）：编码名 → 真实符号
MT2_SYMBOLS: dict[str, str] = {
    "D": "=", "NUL": "\u2212", "C": "+", "two": "\u2208", "one": "\u221e",
    "onesuperior": "{", "ordmasculine": "}", "W": ":", "SUB": "\u2282",
    "exclam": "\u2192", "currency": "\u2260", "backslash": "\u2229",
    "asciicircum": "\u2227", "eight": "\u2200", "dotlessi": "\u2218",
    "DC1": "\u2261", "bracketleft": "\u222a", "dotaccent": "\u00b1",
    "ellipsis": "\u2209", "STX": "\u00d7", "ESC": "\u2283", "r": "\u2228",
    "k": "\u2225", "question": "\u22a5", "four": "\u25b3", "dagger": "\u2220",
    "less": "<", "greater": ">", "SOH": "\u00b7", "j": "|", "n": "\u2216",
    "six": "\u22ee", "Q": "\u03be", "seven": "\u2020", "p": "\u221a",
    "caron": "|", "CR": "\u03b3", "DC2": "\u03b8", "at": "\u2202",
}

# 斜体族（MT2MIT 系）：字母数字原样，符号/希腊字母按下表
MT2_ITALIC: dict[str, str] = {
    "acute": "\u03b6", "period": "(", "slash": ")", "semicolon": ",", "colon": ":",
    "at": "\u2202", "DLE": "\u03be", "CR": "\u03b3", "DC2": "\u03b8",
    "quotesingle": "\u03c6", "SUB": "\u03c1", "ogonek": "\u03b1",
    "quotedbl": "\u03b5", "hungarumlaut": "\u03a9", "dotlessi": "\u03b4",
    "c129": "\u0394", "NUL": "\u2212", "SOH": "\u00b7", "caron": "|",
}

# MSBM10：AMS 黑板体字母（编码名 = 字母）
MSBM_MAP: dict[str, str] = {
    "C": "\u2102", "F": "\ud835\udd3d", "N": "\u2115", "R": "\u211d", "Z": "\u2124",
    "H": "\u210d", "P": "\u2119", "Q": "\u211a",
}

_MT2_FAMILIES: dict[str, dict[str, str]] = {
    "MT2SYT": MT2_SYMBOLS, "MT2SYS": MT2_SYMBOLS, "MT2SYF": MT2_SYMBOLS,
    "MT2SYAT": MT2_SYMBOLS, "MT2SYAS": MT2_SYMBOLS,
    "MT2MIT": MT2_ITALIC, "MT2MIS": MT2_ITALIC, "MT2MIF": MT2_ITALIC,
    "MT2BMIT": MT2_ITALIC,
}


def decode_glyph(font: str, glyph_name: str, ucs: int) -> str:
    """按 (字体, 字形名) 解码单个字形；未知项回退到抽取器给出的字符。

    回退字符若是控制字符（私有编码的未收录项，多为大定界符分片），
    丢弃比留垃圾更可读。
    """
    ch = chr(ucs) if 0 < ucs < 0x10FFFF and not 0xD800 <= ucs <= 0xDFFF else ""
    table = _MT2_FAMILIES.get(font or "")
    if table is not None:
        out = table.get(glyph_name, ch)
    elif (font or "").startswith("MSBM"):
        out = MSBM_MAP.get(glyph_name, ch)
    else:
        out = ch
    # 未收录编码的回退值多为定界符分片/杂符号：控制字符与间距修饰符直接丢弃
    if len(out or "") == 1:
        o = ord(out)
        if (o < 0x20 and out not in "\t\n") or 0x02B0 <= o <= 0x02FF or 0x0300 <= o <= 0x036F:
            return ""
    return out
