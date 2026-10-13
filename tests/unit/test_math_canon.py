r"""数学定界符典则化回归：\(...\)/\[...\] 公式曾被 UI 显示裸文本、被 TeX 导出转义成垃圾。"""

from __future__ import annotations

from src.core.text import normalize_math_text, sanitize_control_chars
from src.export.render import live_blocks
from src.export.tex_export import md_block_to_tex

B = chr(92)
D = "$"


def test_canon_paren_and_bracket_delimiters():
    r"""\(...\) → $...$、\[...\] → $$...$$，公式命令原样保留。"""
    src = "见 " + B + "(x^2" + B + ") 与 " + B + "[" + B + "left|y" + B + "right|" + B + "]"
    out = normalize_math_text(src)
    expect = "见 " + D + "x^2" + D + " 与 " + D * 2 + B + "left|y" + B + "right|" + D * 2
    assert out == expect, (out, expect)


def test_canon_skips_code_fences():
    r"""代码块里的 \(..\) 是字面文本，不得改写。"""
    fence = chr(96) * 3
    src = "前缀 " + fence + chr(10) + "run " + B + "(x" + B + ")" + chr(10) + fence + " 后缀 " + B + "(y" + B + ")"
    out = normalize_math_text(src)
    assert B + "(x" + B + ")" in out, out
    assert D + "y" + D in out, out


def test_canon_preserves_pairing_for_dirty_nested_forms():
    r"""脏嵌套/孤 $ 场景不得改写：转换出的 $ 与相邻 $ 粘成假 $$ 会拆散全篇配对（实测踩过）。"""
    # 形态一：\(..\) 嵌在 $..$ 段内（存量脏写法 $\(\zeta\)_1$）
    src1 = "映射到 $" + B + "(z" + B + ")_1," + B + "(w" + B + ")_n$ 后续"
    out1 = normalize_math_text(src1)
    assert out1 == src1, out1
    # 形态二：孤 $ 之后紧贴 \(（实测块 b_ATvY1Y8QKDfs 的形状）
    src2 = "语义 $9n$ 后 $" + B + "(z" + B + ")_1 尾"
    out2 = normalize_math_text(src2)
    assert D * 2 not in out2, out2
    assert out2.count(D) == src2.count(D), out2


def test_sanitize_drops_remaining_control_chars():
    """0x01-0x07/0x0E-0x1F/0x7F 永不合法：直接删除（实测存量块混过 \x01）。"""
    junk = "a" + chr(1) + "b" + chr(2) + "c" + chr(31) + "d" + chr(127)
    assert sanitize_control_chars(junk) == "abcd"
    assert sanitize_control_chars("合法\t缩进\n换行") == "合法\t缩进\n换行"


def test_live_blocks_heals_existing_content():
    r"""导出公共入口做存量修复：旧块里的 \(..\) 公式与控制字符不得漏进导出。"""
    row = {"id": "b1", "order": 1.0,
           "content": "混入" + chr(1) + "的 " + B + "(a+b" + B + ")",
           "title": "题" + chr(2) + "目"}
    out = live_blocks([row])[0]
    assert out["content"] == "混入的 " + D + "a+b" + D, out["content"]
    assert out["title"] == "题目", out["title"]


def test_tex_export_keeps_latex_commands_raw():
    """TeX 导出：公式命令不得被转义成 \textbackslash 垃圾（曾毁 72 处真实公式）。"""
    body = B + "left|" + B + "sum_{j=1}^n z_j" + B + "right|"
    row = {"id": "b1", "order": 1.0, "content": "恒等式 " + B + "[" + body + B + "]"}
    tex = md_block_to_tex(live_blocks([row])[0]["content"])
    assert B + "left|" in tex, tex
    assert "textbackslash" not in tex, tex
    assert "equation*" in tex, tex
