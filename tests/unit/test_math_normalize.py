"""公式文本规范化回归：跨行行内公式、丢反斜杠、裸希腊字母、垃圾字符。"""

from __future__ import annotations

from src.core.text import normalize_math_text as nm
from src.generate.validate_retry import validate_fill_payload

NL = chr(10)
BS = chr(92)


def test_inline_math_newline_becomes_space():
    """行内公式跨行会让前端不跨行匹配，$ 配对错位 → 后面的散文被当公式渲染。"""
    assert nm("公式 $n=2," + NL + " p=2$ 结束") == "公式 $n=2,  p=2$ 结束"


def test_lost_backslash_restored_from_newline():
    assert nm("满足 $" + NL + "lim_{x}F=1$") == "满足 $" + BS + "lim_{x}F=1$"
    assert nm("设 $(X," + NL + "mathcal{A}," + NL + "mu)$") == \
        "设 $(X," + BS + "mathcal{A}," + BS + "mu)$"


def test_raw_greek_becomes_latex_command():
    assert nm("设 $" + chr(955) + ">0$") == "设 $" + BS + "lambda>0$"
    assert nm("$" + chr(963) + "$-有限性") == "$" + BS + "sigma$-有限性"
    assert nm("$" + chr(916) + "_A F" + BS + "geq 0$") == "$" + BS + "Delta_A F" + BS + "geq 0$"


def test_text_group_keeps_raw_greek():
    src = BS + "text{σ-域 与 μ}"
    assert nm(src) == src


def test_code_fence_untouched():
    fence = chr(96) * 3
    src = "代码块：\n" + fence + "\n$" + NL + "lim x$\n" + fence
    assert nm(src) == src


def test_display_math_keeps_line_breaks():
    src = "$$" + NL + BS + "lambda x + y" + NL + "$$"
    assert nm(src) == src


def test_junk_characters_stripped():
    assert nm("$(X," + chr(0xFB4D) + ")$") == "$(X,)$"


def test_validate_fill_payload_normalizes_math():
    payload = {"content": "设 $" + chr(955) + ">0$ 且推得。" + NL + "下一段",
               "content_format": "md_latex", "tags": [], "sources_patch": [],
               "basis": "synthesis"}
    spec = {"temp_id": "t1", "type": "motivation", "title": "x", "basis": "synthesis",
            "sources": []}
    out = validate_fill_payload(payload, allowed_anchor_ids=set(), anchors_by_id={}, spec=spec)
    assert out["content"].startswith("设 $" + BS + "lambda>0$")
