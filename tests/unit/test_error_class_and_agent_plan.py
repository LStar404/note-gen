"""LLM 错误分类（配置类必须冒泡）与 agent 计划兜底解析（回归：空计划静默 200）。"""

from __future__ import annotations

import pytest

from src.api.agent_edit import _extract_tool_calls
from src.core.errors import LLMError, PriceUnknown, RetryableError
from src.generate.fill import fill_one

AID = "a_92e41455_dc6f1a2453d5ef97"


class RaisingClient:
    def __init__(self, exc):
        self.exc = exc

    def complete(self, *a, **kw):
        raise self.exc


def _spec():
    return {"temp_id": "t1", "type": "definition", "title": "定义", "basis": "anchored",
            "sources": [{"anchor_id": AID, "quote": "原文片段", "relation": "defines"}],
            "depends_on": []}


def _anchors():
    return {AID: {"anchor_id": AID, "content": "原文片段在这里作为锚点正文", "page": 1, "reading_order": 1}}


CFG = {"generate": {"fill_max_in": 8000, "fill_max_out": 6000, "temperature": 0.2}}


def _run(exc):
    return fill_one(RaisingClient(exc), doc_id="d", chapter_id="ch01", spec=_spec(),
                    anchors_by_id=_anchors(), anchors=list(_anchors().values()),
                    model=None, config=CFG, related_titles=[])


def test_config_errors_bubble_not_swallowed():
    """未配置模型/价格未知是配置错误：必须冒泡让任务清晰失败，不得变成「成功的空结果」。"""
    for exc in (PriceUnknown("未配置生成模型（config.yaml generate.models）", code="NO_MODEL"),
                PriceUnknown("成本未知，无法在非零预算下调用")):
        with pytest.raises(PriceUnknown):
            _run(exc)


def test_budget_exceeded_is_isolated():
    """运行期预算用尽按块隔离，保住同章其余块的成果（5.3.5）。"""
    out = _run(PriceUnknown("超出单章成本上限 max_cost_per_chapter", code="BUDGET_EXCEEDED"))
    assert out["_status"] == "failed" and "LLM 调用失败" in out["_errors"][0]


def test_call_errors_still_isolated():
    for exc in (LLMError("LLM 调用被拒绝 402"), RetryableError("LLM 服务返回 503")):
        out = _run(exc)
        assert out["_status"] == "failed"


def test_extract_tool_calls_from_json_plan_fallback():
    """模型用 JSON 正文写计划（MiMo 实测如此）而不是 function-calling 时必须解析出来。"""
    resp = {"output": {"plan": {"intent": "改写",
                                "proposed_changes": [{"tool": "update_block",
                                                      "args": {"block_id": "b_x", "message": "m"}}]}},
            "raw_text": None, "tool_calls": []}
    calls = _extract_tool_calls(resp)
    assert calls == [{"name": "update_block", "args": {"block_id": "b_x", "message": "m"}}]


def test_extract_tool_calls_from_raw_text():
    raw = chr(96) * 3 + 'json\n{"proposed_changes": [{"tool": "create_block", "args": {"chapter_id": "ch01"}}]}\n' + chr(96) * 3
    resp = {"output": None, "raw_text": raw, "tool_calls": []}
    calls = _extract_tool_calls(resp)
    assert calls and calls[0]["name"] == "create_block"


def test_extract_tool_calls_prefers_function_calling():
    resp = {"tool_calls": [{"function": {"name": "update_block",
                                         "arguments": '{"block_id": "b_y", "message": "m"}'}}],
            "output": {"proposed_changes": [{"tool": "wrong", "args": {}}]}}
    calls = _extract_tool_calls(resp)
    assert calls == [{"name": "update_block", "args": {"block_id": "b_y", "message": "m"}}]


def test_extract_tool_calls_empty_when_nothing():
    assert _extract_tool_calls({"output": "自然语言回复，没有计划", "raw_text": "同上"}) == []
