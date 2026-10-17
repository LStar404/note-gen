"""引文救援回归：重试末期吸附不了时丢来源保住整块（不伪造引文），整块转待审核。

现场：线性代数 ch02-05 报告 status=partial，failures 全是
「quote 不匹配锚点原文: a_xxx」×4 —— 扫描书 OCR 原文糊，模型无法逐字照抄，
每次都失败 → 整块判失败。现在末期改为「丢来源保住块 + needs_review」。
"""

from __future__ import annotations

import pytest

from src.core.errors import SchemaViolation
from src.core.text import best_verbatim_quote
from src.generate.fill import fill_one
from src.generate.validate_retry import validate_fill_payload

GOOD = "定理：若 A 可逆，则 A 的行列式不为零，且逆矩阵唯一。"
BAD = "Thenp(o1.,\".\",on)=0  d↓a and d|b then dlax+by for x, yEZ and it follows"

ANCHORS_BY_ID = {
    "a_good": {"anchor_id": "a_good", "content": GOOD, "page": 10, "reading_order": 1},
    "a_bad": {"anchor_id": "a_bad", "content": BAD, "page": 11, "reading_order": 2},
}
ALLOWED = set(ANCHORS_BY_ID)


def _spec() -> dict:
    return {"temp_id": "t1", "type": "motivation", "title": "测试块", "basis": "anchored",
            "sources": [{"anchor_id": "a_good"}, {"anchor_id": "a_bad"}], "depends_on": []}


def _payload() -> dict:
    return {
        "content": "正文内容。", "content_format": "md_latex", "tags": ["线代"],
        "basis": "anchored",
        "sources_patch": [
            {"anchor_id": "a_good", "quote": "A 的行列式不为零", "relation": "states"},
            {"anchor_id": "a_bad", "quote": "模型完全改写过的引文 zzz", "relation": "states"},
        ],
    }


def test_best_verbatim_quote_returns_real_substring():
    q = best_verbatim_quote(GOOD, "若 A 可逆，则 A 的行列式不为零")
    assert q and q in GOOD
    assert best_verbatim_quote(GOOD, "毫不相干的一句话 lorem ipsum") == ""


def test_bad_quote_fails_early_attempts():
    with pytest.raises(SchemaViolation, match="quote 不匹配锚点原文"):
        validate_fill_payload(_payload(), allowed_anchor_ids=ALLOWED,
                              anchors_by_id=ANCHORS_BY_ID, spec=_spec(), attempt=0)
    with pytest.raises(SchemaViolation, match="quote 不匹配锚点原文"):
        validate_fill_payload(_payload(), allowed_anchor_ids=ALLOWED,
                              anchors_by_id=ANCHORS_BY_ID, spec=_spec(), attempt=1)


def test_late_attempt_drops_unfixable_source_and_keeps_block():
    out = validate_fill_payload(_payload(), allowed_anchor_ids=ALLOWED,
                                anchors_by_id=ANCHORS_BY_ID, spec=_spec(), attempt=2)
    ids = [s["anchor_id"] for s in out["sources_patch"]]
    assert ids == ["a_good"], ids                      # 坏来源被移除，好来源保留
    assert out["status"] == "needs_review"             # 整块转待审核
    assert any("移除" in w for w in out.get("_warnings", []))


def test_single_unfixable_source_still_fails():
    payload = _payload()
    payload["sources_patch"] = payload["sources_patch"][1:]       # 只剩坏来源
    spec = _spec()
    spec["sources"] = [{"anchor_id": "a_bad"}]
    with pytest.raises(SchemaViolation, match="quote 不匹配锚点原文"):
        validate_fill_payload(payload, allowed_anchor_ids=ALLOWED,
                              anchors_by_id=ANCHORS_BY_ID, spec=spec, attempt=3)


class _FakeClient:
    """始终返回同一条坏引文的假模型：验证「重试到末期 → 救援 → 交待审核」。"""

    def __init__(self):
        self.calls = 0

    def complete(self, kind, messages, **kwargs):
        self.calls += 1
        return {"output": _payload(), "raw_text": "", "usage": {}}


def test_fill_one_salvages_on_last_attempt():
    client = _FakeClient()
    cfg = {"generate": {"fill_max_out": 4000, "temperature": 0}, "_profile": "textbook"}
    res = fill_one(client, doc_id="doc-x", chapter_id="ch01", spec=_spec(),
                   anchors_by_id=ANCHORS_BY_ID,
                   anchors=list(ANCHORS_BY_ID.values()), model=None, config=cfg,
                   related_titles=[], budget=None, task_id=None)
    assert res["_status"] != "failed", res
    assert res["status"] == "needs_review"
    assert [s["anchor_id"] for s in res["sources_patch"]] == ["a_good"]
    assert client.calls == 3, client.calls        # 前两次让模型自行修正，第三次救援
