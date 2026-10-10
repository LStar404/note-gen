"""4.6.1 sources 归一回归：同一 anchor_id 去重、≤5——原来到 commit 才炸、整章失败。"""

from __future__ import annotations

from src.generate.engine import _normalize_sources
from src.generate.validate_retry import validate_fill_payload


def make_payload(ids: list[str]) -> dict:
    return {"content": "正文", "content_format": "md_latex", "tags": [], "basis": "anchored",
            "sources_patch": [{"anchor_id": i, "quote": "q", "relation": "defines"} for i in ids]}


def test_validate_fill_payload_dedupes_and_caps_sources():
    ids = ["a1", "a1", "a2", "a2", "a3", "a4", "a5", "a6", "a7"]
    payload = make_payload(ids)
    spec = {"temp_id": "t1", "type": "definition", "title": "定义", "basis": "anchored"}
    out = validate_fill_payload(payload, allowed_anchor_ids=set(ids),
                                anchors_by_id={}, spec=spec, check_quotes=False)
    got = [s["anchor_id"] for s in out["sources_patch"]]
    assert got == ["a1", "a2", "a3", "a4", "a5"], got
    # fill.py 丢弃返回值（fill.py:85），归一必须就地改写 payload
    assert [s["anchor_id"] for s in payload["sources_patch"]] == ["a1", "a2", "a3", "a4", "a5"]


def test_validate_fill_payload_preserves_order_and_quotes():
    payload = make_payload(["a2", "a1", "a2"])
    payload["sources_patch"][0]["quote"] = "先"
    spec = {"temp_id": "t1", "type": "definition", "title": "定义", "basis": "anchored"}
    out = validate_fill_payload(payload, allowed_anchor_ids={"a1", "a2"},
                                anchors_by_id={}, spec=spec, check_quotes=False)
    assert [(s["anchor_id"], s["quote"]) for s in out["sources_patch"]] == \
        [("a2", "先"), ("a1", "q")], "保留首次出现"


def test_normalize_sources_keeps_first_and_skips_malformed():
    srcs = [{"anchor_id": "a1", "quote": "x"}, {"anchor_id": "a1", "quote": "y"},
            {"anchor_id": "a2"}, None, {"quote": "no id"}]
    out = _normalize_sources(srcs)
    assert [s["anchor_id"] for s in out] == ["a1", "a2"]


def test_normalize_sources_caps_at_five():
    srcs = [{"anchor_id": f"a{i}"} for i in range(9)]
    assert [s["anchor_id"] for s in _normalize_sources(srcs)] == ["a0", "a1", "a2", "a3", "a4"]
