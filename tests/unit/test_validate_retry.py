"""校验-重试循环分支（5.3.3）：解析失败/schema/业务/LaTeX 闸门。"""

from __future__ import annotations

import pytest

from src.core.errors import SchemaViolation
from src.generate.validate_retry import (check_latex, extract_math_segments,
                                         parse_json_output, validate_fill_payload,
                                         validate_outline_payload)

ANCHOR = "a_92e41455_dc6f1a2453d5ef97"


def test_parse_json_output_repairs():
    assert parse_json_output('{"a": 1}') == {"a": 1}
    assert parse_json_output('{"a": 1,}') == {"a": 1}
    with pytest.raises(SchemaViolation):
        parse_json_output("完全不是 JSON")


def test_outline_validation_ok_and_dag():
    payload = {"blocks": [
        {"temp_id": "t1", "type": "definition", "title": "定义", "basis": "anchored",
         "sources": [{"anchor_id": ANCHOR, "quote": "q", "relation": "defines"}],
         "depends_on": [], "order": 1},
        {"temp_id": "t2", "type": "motivation", "title": "动机", "basis": "synthesis",
         "sources": [], "depends_on": ["t1"], "order": 2},
    ]}
    out = validate_outline_payload(payload, anchor_ids={ANCHOR}, profile="textbook")
    assert len(out) == 2

    bad = {"blocks": [dict(payload["blocks"][0], depends_on=["t1"])]}
    with pytest.raises(SchemaViolation):
        validate_outline_payload(bad, anchor_ids={ANCHOR}, profile="textbook")

    cycle = {"blocks": [
        {"temp_id": "t1", "type": "definition", "title": "a", "basis": "anchored",
         "sources": [{"anchor_id": ANCHOR, "quote": "q", "relation": "defines"}],
         "depends_on": ["t2"], "order": 1},
        {"temp_id": "t2", "type": "motivation", "title": "b", "basis": "synthesis",
         "sources": [], "depends_on": ["t1"], "order": 2},
    ]}
    with pytest.raises(SchemaViolation):
        validate_outline_payload(cycle, anchor_ids={ANCHOR}, profile="textbook")


def test_outline_rejects_unknown_anchor_and_type():
    payload = {"blocks": [{"temp_id": "t1", "type": "definition", "title": "x", "basis": "anchored",
                           "sources": [{"anchor_id": "a_00000000_" + "0" * 16, "quote": "q",
                                        "relation": "defines"}],
                           "depends_on": [], "order": 1}]}
    with pytest.raises(SchemaViolation):
        validate_outline_payload(payload, anchor_ids={ANCHOR}, profile="textbook")
    payload["blocks"][0]["sources"][0]["anchor_id"] = ANCHOR
    payload["blocks"][0]["type"] = "contribution"   # textbook 激活集之外
    with pytest.raises(SchemaViolation):
        validate_outline_payload(payload, anchor_ids={ANCHOR}, profile="textbook")


def test_fill_validation_branches():
    spec = {"type": "definition", "title": "t"}
    anchors = {ANCHOR: {"content": "行列式的定义：设 A 为 n 阶方阵"}}
    good = {"content": "……", "content_format": "md_latex", "tags": [],
            "sources_patch": [{"anchor_id": ANCHOR, "quote": "行列式的定义", "relation": "defines"}],
            "basis": "anchored"}
    validate_fill_payload(good, allowed_anchor_ids={ANCHOR}, anchors_by_id=anchors, spec=spec)

    with pytest.raises(SchemaViolation):   # 引用未提供锚点
        validate_fill_payload(good, allowed_anchor_ids=set(), anchors_by_id=anchors, spec=spec)
    bad_quote = dict(good, sources_patch=[dict(good["sources_patch"][0], quote="不存在的句子")])
    with pytest.raises(SchemaViolation):
        validate_fill_payload(bad_quote, allowed_anchor_ids={ANCHOR}, anchors_by_id=anchors, spec=spec)
    with pytest.raises(SchemaViolation):   # 缺必填字段
        validate_fill_payload({"content": "x"}, allowed_anchor_ids={ANCHOR},
                              anchors_by_id=anchors, spec=spec)


def test_latex_gate():
    ok, warnings = check_latex("正文 $x^2 + y^2 = z^2$ 与 $$\\int_0^1 f(x)dx$$")
    assert ok and not warnings
    ok, warnings = check_latex("$x^{2$")
    assert not ok and warnings
    ok, _ = check_latex("代码块 $x$ 内容")
    assert ok
    segs = extract_math_segments("a $x$ b $$y$$ c")
    assert segs == ["$x$", "$$y$$"]
