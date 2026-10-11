"""提纲/填充流水线回归：首批提纲一出即开始填充，不等整章提纲完成。"""

from __future__ import annotations

import json
import re
import threading

from src.core.store import DocStore
from src.generate.engine import generate_chapter
from tests.integration.test_pipeline import CFG, make_client


def test_fill_starts_before_outline_batches_finish(sample_doc, monkeypatch):
    layout, doc_id = sample_doc
    store = DocStore(layout, doc_id)
    ch = store.chapter_ids()[0]
    anchors = store.anchor_rows(ch)
    assert len(anchors) >= 2
    by_id = {a["anchor_id"]: a for a in anchors}

    order: list[str] = []
    lock = threading.Lock()
    fill_started = threading.Event()
    outline_calls = {"n": 0}

    class Client:
        def complete(self, task, messages, **kw):
            prompt = messages[-1]["content"]
            if task == "outline":
                with lock:
                    outline_calls["n"] += 1
                    n = outline_calls["n"]
                if n == 2:
                    # 第 2 批提纲必须等填充开跑才能返回：流水线失效时只会等满超时，
                    # 顺序断言随后失败（不会死锁）
                    ok = fill_started.wait(15)
                    with lock:
                        order.append("outline_batch2_done" if ok else "outline_batch2_timeout")
                ids = sorted(set(re.findall(r"a_[0-9a-f]+_[0-9a-f]+", prompt)))
                blocks = [{"temp_id": str(j + 1), "type": "definition", "title": f"块{j + 1}",
                           "basis": "anchored",
                           "sources": [{"anchor_id": aid, "quote": by_id[aid]["content"][:20],
                                        "relation": "defines"}],
                           "depends_on": [], "order": j + 1} for j, aid in enumerate(ids)]
                return {"output": {"blocks": blocks}, "raw_text": "{}", "tool_calls": [],
                        "finish_reason": "stop", "usage": {"in": 1, "out": 1},
                        "request_id": "r"}
            # fill：block_spec_json 原样回传 sources（quote 逐字命中锚点原文）
            spec = json.loads(prompt.split("<source_data>")[1].strip().splitlines()[0])
            with lock:
                if not fill_started.is_set():
                    order.append("fill_started")
                fill_started.set()
            out = {"content": "生成的内容。", "content_format": "md_latex", "tags": [],
                   "sources_patch": spec.get("sources", []),
                   "basis": spec.get("basis", "anchored")}
            return {"output": out, "raw_text": "{}", "tool_calls": [],
                    "finish_reason": "stop", "usage": {"in": 1, "out": 1}, "request_id": "r"}

    _, registry = make_client(layout, [])      # 仅借用 registry 注册；调用走下面的假客户端
    cfg = {**CFG, "generate": {**CFG["generate"], "concurrency": 2, "outline_max_out": 32000}}

    # 两批提纲（本测只验证流水线时序，分批策略由 test_fast_start 覆盖）
    monkeypatch.setattr("src.generate.outline._batch_anchors",
                        lambda a, max_in, max_out=float("inf"): [a[:1], a[1:]])

    report = generate_chapter(doc_id, ch, layout=layout, client=Client(), config=cfg,
                              registry=registry, force=True, use_cache=False)
    assert report["status"] == "complete", report
    assert "outline_batch2_timeout" not in order, f"填充未在提纲完成前开跑（流水线失效）: {order}"
    assert order.index("fill_started") < order.index("outline_batch2_done"), order
