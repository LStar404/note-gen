"""公式/图引用闸门回归：未定义宏（KaTeX 红字）与死图占位符都不得入库。"""

from __future__ import annotations

from src.generate.validate_retry import (check_latex, strip_placeholder_figures,
                                         validate_fill_payload)

AID = "a_92e41455_dc6f1a2453d5ef97"


def test_check_latex_flags_undefined_macro_with_suggestion():
    ok, warnings = check_latex(r"设 $\F$ 是 σ-域")
    assert ok is False
    assert any("\\F" in w and "\\mathcal{F}" in w for w in warnings), warnings


def test_check_latex_flags_operatorname_shorthands():
    ok, warnings = check_latex(r"$ \Var(X) = \E[X^2] - \E[X]^2$")
    assert ok is False
    joined = " ".join(warnings)
    assert "\\Var" in joined and "\\E" in joined, warnings


def test_check_latex_accepts_katex_defined_macros():
    for tex in (r"$\mathcal{F}$", r"$\R$", r"$\N \subset \Z$", r"$\operatorname{Var}(X)$"):
        ok, warnings = check_latex(tex)
        assert ok is True, (tex, warnings)


def test_strip_placeholder_figures_removes_dead_refs():
    content = "标题\n\n![fig_00000000]\n\n图注文字"
    assert strip_placeholder_figures(content, {"sources": []}) == "标题\n\n图注文字"


def test_strip_placeholder_figures_keeps_real_refs():
    content = "见图 ![fig_00001200] 所示"
    spec = {"sources": [{"anchor_id": AID, "quote": "q", "relation": "illustrates",
                         "figure_id": "fig_00001200"}]}
    assert strip_placeholder_figures(content, spec) == content


def test_validate_fill_payload_strips_placeholder_at_gate():
    payload = {"content": "![fig_00000000]\n\n图 1：示意图", "content_format": "md_latex",
               "tags": [], "sources_patch": [], "basis": "synthesis"}
    spec = {"temp_id": "t1", "type": "motivation", "title": "x", "basis": "synthesis",
            "sources": []}
    out = validate_fill_payload(payload, allowed_anchor_ids=set(), anchors_by_id={}, spec=spec)
    assert "fig_00000000" not in out["content"]
    assert "图 1：示意图" in out["content"]
