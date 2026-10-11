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
    "six": "\u0338", "Q": "\u03be", "seven": "\u2020", "p": "\u221a",
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

# MT2SYAT/MT2SYAS：关系符族——与 SYT 编码不同！six=\u2264（楔形+横杠）、greater=\u2265
# （2026-10-12 轮廓取证：six/greater 均带横杠，与 SYT 的 six=否定斜杠完全不同）
MT2_RELATIONS: dict[str, str] = {
    "six": "\u2264", "greater": "\u2265",
}

# MT2EXA：大定界符分片（仅收录轮廓/语境确认项）
MT2_BIGOPS: dict[str, str] = {
    "backslash": "\u22c2",    # \u22c2 大交（圆顶两腿，语境 \u22c2_{n=1}^{\u221e}F_n）
    "bracketleft": "\u22c3",  # \u22c3 大并（语境 \u22c3_\u03b3 D_\u03b3）
    "X": "\u2211",            # \u2211 求和（上下分片拼合，语境 nXj=1 → \u2211_{j=1}^n）
}

# MT2SYT.six 是「否定斜杠」，画在被否定符号上（six,DC1→\u2262；six,two→\u2209）。
# 单字形解码只能给出组合符 \u0338；拼接后由本函数归位并合成预组合字符。
_NEG_OVERLAY = "\u0338"


def combine_negations(text: str) -> str:
    r"""把解码流中的否定斜杠组合符移到被否定符号之后并合成预组合字符。

    decode_glyph 对 MT2SYT.six 返回 U+0338（长斜线覆盖符），但该字形画在
    后随符号之上，拼接顺序是「斜杠在前」。本函数把 (\u0338)(X) 转成 X\u0338，
    再做 NFC 归一：\u2261\u0338→≢、\u2208\u0338→∉、=\u0338→≠。
    """
    import re as _re
    import unicodedata as _ud
    moved = _re.sub(_NEG_OVERLAY + "(.)", lambda m: m.group(1) + _NEG_OVERLAY, text)
    return _ud.normalize("NFC", moved)

# MSBM10：AMS 黑板体字母（编码名 = 字母）
MSBM_MAP: dict[str, str] = {
    # 一律用 \U 八位转义：\uD835\uDD3D 这类代理对字面量在 Python 源码里是
    # 两个非法代理字符，进 JSON（ensure_ascii=False）写盘直接 UnicodeEncodeError
    "C": "\u2102", "F": "\U0001D53D", "N": "\u2115", "R": "\u211d", "Z": "\u2124",
    "H": "\u210d", "P": "\u2119", "Q": "\u211a",
}

_MT2_FAMILIES: dict[str, dict[str, str]] = {
    "MT2SYT": MT2_SYMBOLS, "MT2SYS": MT2_SYMBOLS, "MT2SYF": MT2_SYMBOLS,
    "MT2SYAT": MT2_RELATIONS, "MT2SYAS": MT2_RELATIONS,
    "MT2MIT": MT2_ITALIC, "MT2MIS": MT2_ITALIC, "MT2MIF": MT2_ITALIC,
    "MT2BMIT": MT2_ITALIC,
    "MT2EXA": MT2_BIGOPS,
}


# 抽取器（pymupdf/pypdf）给 MT2 字体的文本是「字形名 → AGL 字符」的原样输出
# （named two 的字形抽出 '2'、named quotedbl 抽出 '"'），故按 AGL 反查字形名。
# 只需覆盖各表涉及的字形名；字母的 AGL 名即其本身。
_AGL_CHAR_TO_NAME: dict[str, str] = {
    ".": "period", "/": "slash", ";": "semicolon", ":": "colon",
    chr(34): "quotedbl", "'": "quotesingle", "\u00b4": "acute",
    "\u00b9": "onesuperior", "\u00ba": "ordmasculine", "\u0131": "dotlessi",
    "!": "exclam", "@": "at", "^": "asciicircum", "\\": "backslash",
    "[": "bracketleft", "?": "question", "<": "less", ">": "greater",
    "\u00a4": "currency", "\u2026": "ellipsis", "\u2020": "dagger",
    "\u02d9": "dotaccent", "\u02c7": "caron", "\u02db": "ogonek",
    "\u02dd": "hungarumlaut",
    "0": "zero", "1": "one", "2": "two", "3": "three", "4": "four",
    "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine",
    "\x00": "NUL", "\x01": "SOH", "\x02": "STX", "\x03": "ETX", "\x0d": "CR",
    "\x10": "DLE", "\x11": "DC1", "\x12": "DC2", "\x13": "DC3", "\x14": "DC4",
    "\x1a": "SUB", "\x1b": "ESC", "\x1e": "RS", "\x15": "NAK",
}


def glyph_name_of(ch: str) -> str:
    """抽取字符 → AGL 字形名（字母/未收录字符原样返回）。"""
    return _AGL_CHAR_TO_NAME.get(ch, ch)


def decode_mt2_text(font: str, text: str, *, combine: bool = True) -> str:
    """还原抽取器给出的 MT2 原始文本为真符号（生产抽取路径入口）。

    font 可带子集前缀（如 ABCDEF+MT2MIT）；非 MT2/MSBM 字体原样返回。
    U+FFFD 是控制名字形（NUL/DC1/…）的抽取占位，无法反查，直接丢弃。
    combine=False 时不做否定斜杠合成——供跨 span 拼接后统一合成。
    """
    short = (font or "").split("+")[-1]
    if not (_MT2_FAMILIES.get(short) or short.startswith("MSBM")):
        return text
    out = []
    for ch in text:
        if ch == "\ufffd":
            continue
        out.append(decode_glyph(short, glyph_name_of(ch), ord(ch)))
    joined = "".join(out)
    return combine_negations(joined) if combine else joined


# MT2EXA 是大定界符分片字体：未登记字形是括号/大算符的视觉分片（´、@ 等 AGL 字符
# 纯属占位），输出垃圾不如丢弃；已登记的 backslash/bracketleft 是完整大算符。
_FRAGMENT_FONTS = {"MT2EXA"}


def decode_glyph(font: str, glyph_name: str, ucs: int) -> str:
    """按 (字体, 字形名) 解码单个字形；未知项回退到抽取器给出的字符。

    回退字符若是控制字符（私有编码的未收录项，多为大定界符分片），
    丢弃比留垃圾更可读。
    """
    ch = chr(ucs) if 0 < ucs < 0x10FFFF and not 0xD800 <= ucs <= 0xDFFF else ""
    short = (font or "").split("+")[-1]
    table = _MT2_FAMILIES.get(short)
    if table is not None:
        out = table.get(glyph_name, ch)
        if out == ch and short in _FRAGMENT_FONTS:
            return ""      # 未登记分片：丢弃占位字符
    elif (font or "").startswith("MSBM"):
        out = MSBM_MAP.get(glyph_name, ch)
    else:
        out = ch
    # 未收录编码的回退值多为定界符分片/杂符号：控制字符与间距修饰符直接丢弃。
    # U+0338（否定斜杠组合符，MT2SYT.six）是刻意输出的例外。
    if len(out or "") == 1:
        o = ord(out)
        if o == 0x0338:
            return out
        if (o < 0x20 and out not in "\t\n") or 0x02B0 <= o <= 0x02FF or 0x0300 <= o <= 0x036F:
            return ""
    return out
