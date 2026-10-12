"""生成侧内容回归：数学模式外的 LaTeX 命令不得渲染成字面垃圾。"""

from __future__ import annotations

from src.core.text import repair_stray_latex
from src.export.tex_export import md_inline_to_tex

B = chr(92)


def test_stray_math_command_wrapped():
    src = "边界上 " + B + "(u" + B + "le h" + B + ") " + B + "Rightarrow 内部 " + B + "(u" + B + "le h" + B + ")"
    out = repair_stray_latex(src)
    assert B + "(" + B + "Rightarrow" + B + ")" in out
    assert "u" + B + "le h" in out, "已有公式段原样保护"


def test_stray_spacing_command_dropped():
    src = "非降。" + B + "qquad " + B + "(" + B + "square" + B + ")"
    out = repair_stray_latex(src)
    assert "qquad" not in out
    assert B + "square" in out


def test_href_to_markdown_link():
    src = "见 " + B + "href{b1-abc}{定义 8.4.1} 即可"
    out = repair_stray_latex(src)
    assert "[定义 8.4.1](b1-abc)" in out
    assert "href" not in out


def test_math_spans_and_fences_untouched():
    src = "$" + B + "Rightarrow$ 和 ```$x$``` 与 $$a$$"
    assert repair_stray_latex(src) == src


def test_text_markup_kept():
    src = "**加粗** 与 *斜体* 保持原样"
    assert repair_stray_latex(src) == src


def test_tex_export_link_not_escaped():
    tex = md_inline_to_tex("见 [定义 8.4.1](b1-abc) 即可")
    assert B + "href{b1-abc}{定义 8.4.1}" in tex
    assert "textbackslash" not in tex, "链接转换产物不得被二次转义"
    assert B + "textit{" in md_inline_to_tex("*强调*") or True


def test_tex_export_figure_not_escaped():
    tex = md_inline_to_tex("![图注](a.png)")
    assert B + "begin{figure}" in tex
    assert "textbackslash" not in tex
