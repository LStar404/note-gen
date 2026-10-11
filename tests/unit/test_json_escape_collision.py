"""JSON 转义撞车回归：裸 LaTeX（\bigcap）被 JSON 吃掉反斜杠，渲染成 "Digcap" 破公式。"""

from __future__ import annotations

from src.core.text import repair_json_escapes, sanitize_control_chars
from src.generate.validate_retry import parse_json_output, validate_fill_payload


def test_repair_rewrites_bare_latex_escapes():
    raw = '{"content": "$\\bigcap_{n=1}^{\\infty} A_n$ 与 $\\theta$、$\\frac{1}{2}$"}'
    payload = parse_json_output(repair_json_escapes(raw))
    assert payload["content"] == "$\\bigcap_{n=1}^{\\infty} A_n$ 与 $\\theta$、$\\frac{1}{2}$"


def test_repair_keeps_real_escapes():
    raw = '{"content": "第一行\\n第二行\\t制表\\u0041"}'
    assert repair_json_escapes(raw) == raw, "合法转义（换行/制表/\\uXXXX）不得被改写"
    assert parse_json_output(raw)["content"] == "第一行\n第二行\t制表A"


def test_parse_json_output_fixes_collision_end_to_end():
    """模型原样输出单反斜杠 LaTeX —— 这正是线上出问题的那种输出。"""
    raw = '{"content": "$\\nu$、$\\nabla f$、$\\times$、$\\underline{x}$"}'
    assert parse_json_output(raw)["content"] == "$\\nu$、$\\nabla f$、$\\times$、$\\underline{x}$"


def test_sanitize_control_chars_restores_commands():
    assert sanitize_control_chars(chr(8) + "igcap_{n=1}") == "\\bigcap_{n=1}"
    assert sanitize_control_chars(chr(12) + "rac{1}{2}") == "\\frac{1}{2}"
    assert sanitize_control_chars(chr(9) + "heta") == "\\theta"
    assert sanitize_control_chars(chr(0) + "mathbb{Q}") == "\\mathbb{Q}"
    # 合法的换行/制表符不动（换行后接普通文字）
    assert sanitize_control_chars("正常\n换行") == "正常\n换行"
    assert sanitize_control_chars("a\tb") == "a\tb"


def test_validate_fill_payload_sanitizes_at_gate():
    payload = {"content": "$" + chr(8) + "igcap_i A_i$", "content_format": "md_latex",
               "tags": [], "sources_patch": [], "basis": "synthesis"}
    spec = {"temp_id": "t1", "type": "motivation", "title": "x", "basis": "synthesis",
            "sources": []}
    out = validate_fill_payload(payload, allowed_anchor_ids=set(), anchors_by_id={}, spec=spec)
    assert out["content"] == "$\\bigcap_i A_i$"
