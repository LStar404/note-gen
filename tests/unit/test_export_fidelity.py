"""导出回归：跨行显示公式不得被文本转义；Anki 字段必须 HTML 转义。"""

from __future__ import annotations

from src.export.anki_export import _back_html
from src.export.tex_export import md_block_to_tex


def test_multiline_display_math_not_escaped():
    content = """设 f 满足：
$$
\u005cfrac{1}{1-z}=\u005csum_{n=0}^{\u005cinfty} \u005cfrac{(z-z_0)^n}{(1-z_0)^{n+1}},
\u005ctag{6.2.1}
$$
其收敛半径为 R。"""
    tex = md_block_to_tex(content)
    assert "\u005cbegin{equation*}\u005cfrac{1}{1-z}=\u005csum" in tex
    assert "textbackslash" not in tex, "公式续行不得被文本转义"
    assert "\u005cend{equation*}" in tex
    # 正文行仍走行内转换
    assert "R。" in tex


def test_inline_display_math_still_works():
    tex = md_block_to_tex("看 $$x^2+y^2$$ 即可。")
    assert "\u005cbegin{equation*}x^2+y^2\u005cend{equation*}" in tex
    assert "textasciicircum" not in tex


def test_heading_inline_math_not_escaped():
    tex = md_block_to_tex("## 命题 8.4.5 $\u005clog|f|$ 是次调和函数")
    assert "subsection{命题 8.4.5 \u005c(\u005clog|f|\u005c) 是次调和函数}" in tex
    assert "textbackslash" not in tex


def test_anki_back_renders_markdown_not_literal():
    """Anki 不渲染 Markdown：卡背的 **加粗**/链接/列表曾以字面语法显示。"""
    block = {"content": "**重点** 与 *斜体* 与 `代码`\n- 条目一\n[站内](note://anchor/a?page=3) 与 [外链](https://x.y)",
             "sources": []}
    out = _back_html(block, {})
    assert "<b>重点</b>" in out and "<i>斜体</i>" in out and "<code>代码</code>" in out, out
    assert "• 条目一" in out, out
    assert '<a href="https://x.y">外链</a>' in out, out
    assert "note://" not in out and "[站内]" not in out, "站内链接应降级纯文本"


def test_anki_math_protected_from_markdown_and_lt_escaped():
    """数学段里的 * 是乘号不是斜体；< 必须转义（HTML 吞标签老 bug 不得回归）。"""
    block = {"content": "$a * b < c$ 与 " + chr(92) + "(**x**" + chr(92) + ")", "sources": []}
    out = _back_html(block, {})
    assert "<i>" not in out and "<b>" not in out, out
    assert "&lt;" in out, out


def test_anki_back_html_escapes_lt():
    block = {"content": "条件 \u005c(R<\u005cinfty\u005c) 成立\n第二行", "sources": []}
    h = _back_html(block, {})
    assert "&lt;" in h and "R<\u005cinfty" not in h, "数学中的 < 必须转义，否则被当 HTML 标签吞掉"
    assert "<br/>" in h, "换行仍是合法 HTML"
    assert "\u005c(" in h, "MathJax 定界符保留"
