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


def test_anki_back_html_escapes_lt():
    block = {"content": "条件 \u005c(R<\u005cinfty\u005c) 成立\n第二行", "sources": []}
    h = _back_html(block, {})
    assert "&lt;" in h and "R<\u005cinfty" not in h, "数学中的 < 必须转义，否则被当 HTML 标签吞掉"
    assert "<br/>" in h, "换行仍是合法 HTML"
    assert "\u005c(" in h, "MathJax 定界符保留"
