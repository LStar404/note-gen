"""quote 吸附（4.6.1）：模型改写引文时回切原文逐字片段，而非整块丢弃。"""

from __future__ import annotations

import pytest

from src.core.models import find_quote_anchor, quote_matches, snap_quote
from src.core.errors import SchemaViolation
from src.generate.validate_retry import validate_fill_payload

ANCHOR = "a_92e41455_dc6f1a2453d5ef97"
CONTENT = "设 $f(z)$ 在区域 $D$ 内全纯，则 $f$ 在 $D$ 内任意阶可导。"
ANCHORS = {ANCHOR: {"anchor_id": ANCHOR, "content": CONTENT, "page": 1, "reading_order": 1}}


def test_snap_recovers_whitespace_variant():
    """LaTeX 空格差异（$f(z)$ vs $f(z) $）不该判失败。"""
    bad = "设 $f(z) $ 在区域 $D$ 内全纯"
    fixed = snap_quote(CONTENT, bad)
    assert fixed is not None
    assert fixed in CONTENT, "返回值必须是原文连续子串"
    assert quote_matches(CONTENT, fixed)


def test_snap_recovers_punctuation_merge():
    bad = "在区域$D$内全纯，则$f$在$D$内任意阶可导。"
    fixed = snap_quote(CONTENT, bad)
    assert fixed is not None and fixed in CONTENT


def test_snap_rejects_fabricated_quote():
    assert snap_quote(CONTENT, "这句话完全是模型编造的引文") is None
    assert snap_quote(CONTENT, "") is None
    assert snap_quote("", "x") is None


def test_validate_fill_payload_repairs_quote_in_place():
    payload = {"content": "内容", "content_format": "md_latex", "tags": [],
               "sources_patch": [{"anchor_id": ANCHOR, "quote": "设 $f(z) $ 在区域 $D$ 内全纯",
                                  "relation": "defines"}],
               "basis": "anchored"}
    out = validate_fill_payload(payload, allowed_anchor_ids={ANCHOR}, anchors_by_id=ANCHORS,
                                spec={"type": "definition", "title": "t"})
    assert out["sources_patch"][0]["quote"] in CONTENT, "校验应把 quote 修回原文逐字片段"


def test_find_quote_anchor_reassigns_misattributed_quote():
    """模型把 B 锚点原文挂到 A 名下：唯一逐字命中应改挂回真实出处。"""
    a = {"anchor_id": "a_1", "content": "第一段讲柯西定理的条件"}
    b = {"anchor_id": "a_2", "content": "第二段讲刘维尔定理的推论"}
    anchors = {"a_1": a, "a_2": b}
    hit = find_quote_anchor("第二段讲刘维尔定理的推论", anchors, {"a_1", "a_2"})
    assert hit == ("a_2", "第二段讲刘维尔定理的推论")


def test_find_quote_anchor_rejects_ambiguous_or_absent():
    a = {"anchor_id": "a_1", "content": "相同内容的段落"}
    b = {"anchor_id": "a_2", "content": "相同内容的段落"}
    anchors = {"a_1": a, "a_2": b}
    assert find_quote_anchor("相同内容的段落", anchors, {"a_1", "a_2"}) is None, "多命中不猜"
    assert find_quote_anchor("根本不存在的句子", anchors, {"a_1", "a_2"}) is None


def test_validate_fill_payload_reassigns_wrong_anchor():
    payload = {"content": "内容", "content_format": "md_latex", "tags": [],
               "sources_patch": [{"anchor_id": ANCHOR,
                                  "quote": "第二段才是这段引文的真实出处",
                                  "relation": "defines"}],
               "basis": "anchored"}
    anchors = dict(ANCHORS)
    anchors["a_other"] = {"anchor_id": "a_other", "content": "第二段才是这段引文的真实出处",
                          "page": 2, "reading_order": 1}
    out = validate_fill_payload(payload, allowed_anchor_ids={ANCHOR, "a_other"},
                                anchors_by_id=anchors,
                                spec={"type": "definition", "title": "t"})
    assert out["sources_patch"][0]["anchor_id"] == "a_other", "应改挂回真实出处锚点"
    assert out["sources_patch"][0]["quote"] in anchors["a_other"]["content"]


def test_validate_fill_payload_still_rejects_fabrication():
    payload = {"content": "内容", "content_format": "md_latex", "tags": [],
               "sources_patch": [{"anchor_id": ANCHOR, "quote": "凭空捏造的引用内容",
                                  "relation": "defines"}],
               "basis": "anchored"}
    with pytest.raises(SchemaViolation):
        validate_fill_payload(payload, allowed_anchor_ids={ANCHOR}, anchors_by_id=ANCHORS,
                              spec={"type": "definition", "title": "t"})
