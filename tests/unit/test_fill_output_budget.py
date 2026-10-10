"""fill 输出预算与 quote 重试提示（回归：max_tokens=2000 截断 JSON 致整块丢弃）。"""

from __future__ import annotations

from src.generate.fill import fill_one

AID = "a_92e41455_dc6f1a2453d5ef97"


class Spy:
    """记录每次 complete 的 max_tokens 与 messages，按序返回脚本化响应。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def complete(self, *a, **kw):
        self.calls.append({"max_tokens": kw.get("max_tokens"),
                           "messages": kw.get("messages") or (a[1] if len(a) > 1 else [])})
        return self.responses.pop(0)


def _resp(output):
    return {"output": output, "raw_text": "{}", "tool_calls": [], "finish_reason": "stop",
            "usage": {"in": 1, "out": 1, "estimated": True}, "request_id": "r"}


def _spec():
    return {"temp_id": "t1", "type": "definition", "title": "定义", "basis": "anchored",
            "sources": [{"anchor_id": AID, "quote": "原文片段", "relation": "defines"}],
            "depends_on": []}


def _anchors():
    return {AID: {"anchor_id": AID, "content": "原文片段在这里作为锚点正文", "page": 1, "reading_order": 1}}


def _good():
    return {"content": "内容", "content_format": "md_latex", "tags": [],
            "sources_patch": [{"anchor_id": AID, "quote": "原文片段", "relation": "defines"}],
            "basis": "anchored"}


def _cfg(**extra):
    base = {"fill_max_in": 8000, "fill_max_out": 6000, "temperature": 0.2}
    base.update(extra)
    return {"generate": base}


def test_fill_uses_configured_output_budget():
    """fill_max_out 必须传给 LLM；写死 2000 会截断长块 JSON（实测 ch06 丢 12/48 块）。"""
    spy = Spy([_resp(_good())])
    fill_one(spy, doc_id="d", chapter_id="ch01", spec=_spec(),
             anchors_by_id=_anchors(), anchors=list(_anchors().values()),
             model=None, config=_cfg(), related_titles=[])
    assert spy.calls[0]["max_tokens"] == 6000


def test_fill_output_budget_defaults_to_6000():
    spy = Spy([_resp(_good())])
    fill_one(spy, doc_id="d", chapter_id="ch01", spec=_spec(),
             anchors_by_id=_anchors(), anchors=list(_anchors().values()),
             model=None, config={"generate": {"fill_max_in": 8000, "temperature": 0.2}},
             related_titles=[])
    assert spy.calls[0]["max_tokens"] == 6000


def test_quote_mismatch_retry_hint_is_actionable():
    """quote 不匹配时重试提示必须点名「逐字照抄」，否则模型只会再次改写。"""
    bad = dict(_good(), sources_patch=[{"anchor_id": AID, "quote": "被改写的引用", "relation": "defines"}])
    spy = Spy([_resp(bad), _resp(_good())])
    out = fill_one(spy, doc_id="d", chapter_id="ch01", spec=_spec(),
                   anchors_by_id=_anchors(), anchors=list(_anchors().values()),
                   model=None, config=_cfg(), related_titles=[])
    assert out.get("_status") != "failed", "第二次应成功"
    retry_msg = spy.calls[1]["messages"][-1]["content"]
    assert "逐字照抄" in retry_msg and "quote" in retry_msg


def test_json_error_retry_hint_forbids_fences_and_truncation():
    """截断/围栏导致的非法 JSON，重试提示须明示禁止代码围栏与截断。"""
    truncated = chr(96) * 3 + "json\n{\"content\": "
    spy = Spy([_resp(truncated), _resp(_good())])
    fill_one(spy, doc_id="d", chapter_id="ch01", spec=_spec(),
             anchors_by_id=_anchors(), anchors=list(_anchors().values()),
             model=None, config=_cfg(), related_titles=[])
    retry_msg = spy.calls[1]["messages"][-1]["content"]
    assert "完整 JSON 对象" in retry_msg and "代码围栏" in retry_msg
