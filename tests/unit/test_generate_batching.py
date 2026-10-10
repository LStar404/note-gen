"""生成链路回归测试（B1–B5）：输出预算分批、截断缩批 temp_id 唯一、取消、进度、任务结局。"""

from __future__ import annotations

import re
import threading

import pytest

from src.api.tasks import TaskManager
from src.core.errors import TaskCancelled
from src.db.runtime import get_runtime
from src.generate.fill import fill_all
from src.generate.outline import _OUT_RESERVE, _anchor_cost, _batch_anchors, generate_outline


def mk_anchor(i: int, content_len: int = 300) -> dict:
    aid = f"a_ns_{i:016d}"
    text = f"ANCH{i} 本段原文。" + "原" * content_len
    return {"anchor_id": aid, "doc_id": "doc-x", "chapter_id": "ch01", "page": i + 1,
            "reading_order": 0, "heading": f"第 {i} 节 ANCH{i}", "content": text,
            "text": text, "quote": text[:40], "kind": "paragraph"}


class FakeOutlineClient:
    """锚点数 > truncate_above 的批返回截断（finish_reason=length）逼出二分缩批。"""

    def __init__(self, truncate_above: int = 2):
        self.truncate_above = truncate_above
        self.calls = 0

    def complete(self, task, messages, **kw):
        self.calls += 1
        prompt = messages[-1]["content"]
        ids = sorted(set(re.findall(r"a_ns_[0-9a-f]+", prompt)))
        marks = sorted(set(re.findall(r"ANCH(\d+)", prompt)), key=int)
        if len(marks) > self.truncate_above:
            return {"output": None, "raw_text": '{"blocks": [', "tool_calls": [],
                    "finish_reason": "length", "usage": {"in": 1, "out": 1}, "request_id": "r"}
        blocks = []
        for j, aid in enumerate(ids):
            blocks.append({
                "temp_id": str(j + 1), "type": "definition", "title": f"块 {aid}",
                "basis": "anchored",
                "sources": [{"anchor_id": aid, "quote": "原文", "relation": "defines"}],
                "depends_on": [str(j)] if j else [], "order": j + 1,
            })
        return {"output": {"blocks": blocks}, "raw_text": "{}", "tool_calls": [],
                "finish_reason": "stop", "usage": {"in": 1, "out": 1}, "request_id": "r"}


CFG = {"generate": {"outline_max_in": 10 ** 9, "outline_max_out": 10 ** 9,
                    "concurrency": 2, "temperature": 0.2, "output_language": "zh"}}


def call_outline(client, anchors, **kw):
    return generate_outline(client, doc_id="doc-x", chapter_id="ch01",
                            manifest={"profile": "textbook", "title": "书", "chapters": []},
                            anchors=anchors, model=None, config=CFG, **kw)


# ---- B5：分批同时受输出预算约束 ----

def test_batch_anchors_respects_output_budget():
    anchors = [mk_anchor(i) for i in range(10)]
    max_out = 2000.0
    batches = _batch_anchors(anchors, max_in=10 ** 9, max_out=max_out)
    assert len(batches) > 1, "输出预算应把大批切开"
    for b in batches:
        est_out = sum(_anchor_cost(a)[1] for a in b)
        assert est_out + _OUT_RESERVE <= max_out or len(b) == 1, f"批输出估算 {est_out} 超出 {max_out}"
    flat = [a for b in batches for a in b]
    assert flat == sorted(flat, key=lambda a: (a["page"], a["reading_order"])), "分批不得打乱阅读序"


def test_batch_anchors_still_respects_input_budget():
    anchors = [mk_anchor(i) for i in range(10)]
    batches = _batch_anchors(anchors, max_in=2500, max_out=float("inf"))
    assert len(batches) > 1
    for b in batches:
        est_in = sum(_anchor_cost(a)[0] for a in b)
        assert est_in + 1500 <= 2500 or len(b) == 1


# ---- B1：截断缩批后 temp_id 全局唯一、依赖指向存在 ----

def test_temp_ids_unique_across_truncation_halves():
    anchors = [mk_anchor(i) for i in range(5)]
    client = FakeOutlineClient(truncate_above=2)   # 5 → 2+3 → 3 再截成 1+2
    res = call_outline(client, anchors)
    ids = [it["temp_id"] for it in res.items]
    assert len(ids) == 5, f"块数不对: {ids}"
    assert len(set(ids)) == len(ids), f"temp_id 跨调用撞名: {ids}"
    for t in ids:
        assert re.fullmatch(r"[A-Za-z0-9-]{1,64}", t), t
    known = set(ids)
    for it in res.items:
        for d in it["depends_on"]:
            assert d in known, f"depends_on 指向不存在的 temp_id: {d}（{ids}）"
    assert client.calls >= 3, "应发生缩批递归"


def test_temp_ids_prefix_is_path_encoded_not_bare_t():
    anchors = [mk_anchor(i) for i in range(3)]
    client = FakeOutlineClient(truncate_above=1)
    res = call_outline(client, anchors)
    for it in res.items:
        assert not it["temp_id"].startswith("t-"), it["temp_id"]
        assert it["temp_id"].startswith("b"), it["temp_id"]


# ---- B2：调用内取消立即生效 ----

def test_outline_cancel_raises_task_cancelled():
    client = FakeOutlineClient()
    with pytest.raises(TaskCancelled):
        call_outline(client, [mk_anchor(0)], is_cancelled=lambda: True)
    assert client.calls == 0, "取消后不应发出任何 LLM 调用"


# ---- B3：进度回调逐批上报 ----

def test_outline_progress_reports_batches():
    events = []
    client = FakeOutlineClient(truncate_above=2)
    call_outline(client, [mk_anchor(i) for i in range(4)], progress=events.append)
    outline_events = [e for e in events if e.get("stage") == "outline"]
    assert outline_events, f"没有提纲进度事件: {events}"
    for e in outline_events:
        assert 1 <= e["batch"] <= e["batches"]
    assert any(e["batch"] == e["batches"] for e in outline_events)


def test_fill_all_cancel_raises_task_cancelled():
    class Boom:
        def complete(self, *a, **kw):
            raise AssertionError("取消后不应调用 LLM")

    items = [{"temp_id": "t1", "type": "definition", "title": "x", "basis": "anchored",
              "sources": [], "depends_on": [], "order": 1}]
    with pytest.raises(TaskCancelled):
        fill_all(Boom(), doc_id="doc-x", chapter_id="ch01", outline_items=items,
                 anchors_by_id={}, anchors=[], model=None,
                 config={"generate": {"fill_max_in": 1000}}, is_cancelled=lambda: True)


# ---- B4/B3：TaskManager 传 task_id、取消后不改写结局 ----

def test_task_manager_passes_task_id_and_keeps_cancelled(tmp_path):
    rt = get_runtime(str(tmp_path / "runtime.db"))
    tm = TaskManager(rt)
    seen: dict = {}
    started = threading.Event()
    release = threading.Event()

    def fn(request, progress, is_cancelled, register_artifact, task_id=None):
        seen["task_id"] = task_id
        started.set()
        assert release.wait(10)
        progress({"stage": "x", "n": 1})
        return {"ok": True}

    task = tm.submit("generate", fn, {"doc_id": "d1"}, doc_id="d1")
    assert started.wait(10)
    assert seen["task_id"] == task["task_id"], "fn 必须拿到 task_id"

    tm.cancel(task["task_id"])
    release.set()
    with tm._lock:
        th = tm._threads[task["task_id"]]
    th.join(10)

    final = tm.get(task["task_id"])
    assert final["status"] == "cancelled", "取消后 fn 正常返回不得把结局改写为 succeeded"
    assert final["result"] is None
