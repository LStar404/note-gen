"""LLM 层失败隔离（回归：402 中断整章丢掉全部已完成填充）。"""

from __future__ import annotations

from src.core.errors import LLMError, RetryableError
from src.generate.fill import fill_all, fill_one
from src.generate.outline import generate_outline

AID = "a_92e41455_dc6f1a2453d5ef97"


class FlakyClient:
    """按调用序返回脚本化结果；元素为 Exception 时抛出。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def complete(self, *a, **kw):
        self.calls += 1
        item = self.script.pop(0) if self.script else self.script_last
        if isinstance(item, Exception):
            raise item
        return item


def _fill_resp(good=True):
    out = {"content": "内容", "content_format": "md_latex", "tags": [],
           "sources_patch": [{"anchor_id": AID, "quote": "原文片段", "relation": "defines"}],
           "basis": "anchored"}
    return {"output": out, "raw_text": "{}", "tool_calls": [], "finish_reason": "stop",
            "usage": {"in": 1, "out": 1, "estimated": True}, "request_id": "r"}


def _outline_resp(tids):
    blocks = [{"temp_id": t, "type": "definition", "title": "定义" + t, "basis": "anchored",
               "sources": [{"anchor_id": AID, "quote": "原文片段", "relation": "defines"}],
               "depends_on": [], "order": i + 1} for i, t in enumerate(tids)]
    return {"output": {"blocks": blocks}, "raw_text": "{}", "tool_calls": [],
            "finish_reason": "stop", "usage": {"in": 1, "out": 1, "estimated": True},
            "request_id": "r"}


def _spec(tid="t1"):
    return {"temp_id": tid, "type": "definition", "title": "定义", "basis": "anchored",
            "sources": [{"anchor_id": AID, "quote": "原文片段", "relation": "defines"}],
            "depends_on": []}


def _anchors():
    return {AID: {"anchor_id": AID, "content": "原文片段在这里作为锚点正文", "page": 1, "reading_order": 1}}


CFG = {"generate": {"fill_max_in": 8000, "fill_max_out": 6000, "temperature": 0.2,
                    "outline_max_in": 32000, "outline_max_out": 16000, "concurrency": 1}}


def test_fill_one_llm_error_is_isolated():
    """余额耗尽等 LLM 错误必须转成该块的失败记录，不得冒泡杀掉整章。"""
    for exc in (LLMError("LLM 调用被拒绝 402（供应商 deepseek，模型 deepseek-flash）"),
                RetryableError("LLM 服务返回 503")):
        spy = FlakyClient([exc])
        out = fill_one(spy, doc_id="d", chapter_id="ch01", spec=_spec(),
                       anchors_by_id=_anchors(), anchors=list(_anchors().values()),
                       model=None, config=CFG, related_titles=[])
        assert out["_status"] == "failed"
        assert "LLM 调用失败" in out["_errors"][0]


def test_fill_all_survives_partial_llm_failure():
    """一块 LLM 失败不得丢弃其余块的填充结果。"""
    spy = FlakyClient([_fill_resp(), LLMError("LLM 调用被拒绝 402")])
    filled = fill_all(spy, doc_id="d", chapter_id="ch01",
                      outline_items=[_spec("t1"), _spec("t2")],
                      anchors_by_id=_anchors(), anchors=list(_anchors().values()),
                      model=None, config=CFG, concurrency=1)
    assert set(filled) == {"t1", "t2"}
    statuses = {t: v["_status"] for t, v in filled.items()}
    assert list(statuses.values()).count("ok") == 1
    assert list(statuses.values()).count("failed") == 1


def test_outline_batch_failure_keeps_other_batches():
    """单批 LLM 失败只影响该批：其余批的条目保留，outline_complete=False 保住旧块。"""
    anchors = [{"anchor_id": AID, "content": "原文片段", "page": 1, "reading_order": 1},
               {"anchor_id": "a_92e41455_dc6f1a2453d5ef98", "content": "第二段锚点原文",
                "page": 2, "reading_order": 1}]
    manifest = {"profile": "textbook", "title": "书", "chapters": [{"chapter_id": "ch01", "title": "第一章"}]}
    cfg = dict(CFG, generate={**CFG["generate"], "outline_max_in": 1})   # 每锚点一批 → 2 批
    spy = FlakyClient([LLMError("LLM 调用被拒绝 402"), _outline_resp(["b-a"])])
    res = generate_outline(spy, doc_id="d", chapter_id="ch01", manifest=manifest,
                           anchors=anchors, model=None, config=cfg)
    assert res.batches == 2
    # 第 0 批 LLM 失败（隔离为空），第 1 批成功——temp_id 前缀带批次号 b1
    assert [i["temp_id"] for i in res.items] == ["b1-b-a"], res.items
    assert res.complete is False and any("LLM 调用失败" in e for e in res.errors)
