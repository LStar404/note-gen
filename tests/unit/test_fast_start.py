"""任务启动提速回归：提纲小批分批、逐批流出、motivation 配额按批折算。"""

from __future__ import annotations

import re

from src.generate.outline import generate_outline


def mk_anchor(i: int) -> dict:
    aid = f"a_{i:08x}_dddddddddddd"
    text = f"ANCH{i} 本段原文。" + "原" * 200
    return {"anchor_id": aid, "doc_id": "doc-x", "chapter_id": "ch01", "page": i + 1,
            "reading_order": 0, "heading": f"第 {i} 节", "content": text, "text": text,
            "quote": text[:40], "kind": "paragraph"}


class RecordingClient:
    """记录每次调用的任务/上限/提示词，按提示词里的锚点 id 出提纲。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def complete(self, task, messages, **kw):
        prompt = messages[-1]["content"]
        self.calls.append({"task": task, "max_tokens": kw.get("max_tokens"), "prompt": prompt})
        ids = sorted(set(re.findall(r"a_[0-9a-f]+_[0-9a-f]+", prompt)))
        blocks = [{"temp_id": str(j + 1), "type": "definition", "title": f"块{j + 1}",
                   "basis": "anchored",
                   "sources": [{"anchor_id": aid, "quote": "原文", "relation": "defines"}],
                   "depends_on": [], "order": j + 1} for j, aid in enumerate(ids)]
        return {"output": {"blocks": blocks}, "raw_text": "{}", "tool_calls": [],
                "finish_reason": "stop", "usage": {"in": 1, "out": 1}, "request_id": "r"}


CFG = {"generate": {"outline_max_in": 10 ** 9, "outline_max_out": 32000,
                    "outline_batch_out": 2000, "concurrency": 1,
                    "temperature": 0.2, "output_language": "zh"}}


def call_outline(client, anchors, batch_out=None, **kw):
    cfg = CFG
    if batch_out is not None:
        cfg = {**CFG, "generate": {**CFG["generate"], "outline_batch_out": batch_out}}
    return generate_outline(client, doc_id="doc-x", chapter_id="ch01",
                            manifest={"profile": "textbook", "title": "书", "chapters": []},
                            anchors=anchors, model=None, config=cfg, **kw)


def test_outline_batch_out_drives_batching_not_api_cap():
    """分批目标（启动快慢）与 API max_tokens 上限（防截断）必须解耦。"""
    anchors = [mk_anchor(i) for i in range(10)]
    client = RecordingClient()
    res = call_outline(client, anchors)
    assert res.batches > 1, "outline_batch_out 应把提纲切成多批，首批才能早点出结果"
    assert len(res.items) == 10
    assert all(c["max_tokens"] == 32000 for c in client.calls), \
        "缩小分批不得压低 API max_tokens 上限（截断会触发缩批重试，反而更慢）"

    # 对照：分批目标给大 → 恢复单批
    res2 = call_outline(RecordingClient(), anchors, batch_out=10 ** 9)
    assert res2.batches == 1


def test_outline_streams_items_per_batch():
    """逐批流出：on_items 在每批校验通过后立即给出该批条目（流水线的输入）。"""
    streamed: list[dict] = []
    res = call_outline(RecordingClient(), [mk_anchor(i) for i in range(10)],
                       on_items=streamed.extend)
    assert len(streamed) == 10
    assert {i["temp_id"] for i in streamed} == {i["temp_id"] for i in res.items}


def test_n_motivation_scales_with_batch_share():
    """motivation 配额随章体量与批占比缩放：小章不再每批喊「最多 6 个」注水。"""
    anchors = [mk_anchor(i) for i in range(10)]
    client = RecordingClient()
    call_outline(client, anchors)                      # 小章（10 锚点）→ 配额 1
    assert all("最多 1 个" in c["prompt"] for c in client.calls), \
        [c["prompt"][c["prompt"].find("motivation"):][:24] for c in client.calls]

    big = [dict(mk_anchor(i), content="短锚点") for i in range(80)]
    client2 = RecordingClient()
    call_outline(client2, big, batch_out=10 ** 9)      # 大章单批 → 配额随目标块数升至 4
    assert len(client2.calls) == 1, "短锚点应单批"
    assert "最多 4 个" in client2.calls[0]["prompt"]
