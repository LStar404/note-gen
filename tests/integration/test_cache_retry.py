"""缓存失败重试（回归：重跑同一章只是复读旧失败，块永远补不回来）。"""

from __future__ import annotations

import json

from src.core.llm import DummyProvider, LLMClient
from src.core.model_registry import ModelRegistry
from src.generate.engine import generate_chapter


class FakeRuntime:
    def log_usage(self, **kw):
        pass


def _resp(out):
    return {"output": out, "raw_text": "{}", "tool_calls": [], "finish_reason": "stop",
            "usage": {"in": 1, "out": 1, "estimated": True}, "request_id": "r"}


def test_cached_failures_are_retried_on_rerun(sample_doc, monkeypatch):
    """缓存里带 failed 候选时重跑必须重试填充，成功后并入结果。"""
    from src.core.store import DocStore

    layout, doc_id = sample_doc
    anchors = DocStore(layout, doc_id).anchor_rows("ch01")
    aid = anchors[1]["anchor_id"]
    quote = anchors[1]["content"][:6]
    spec = {"temp_id": "t1", "type": "definition", "title": "定义", "basis": "anchored",
            "sources": [{"anchor_id": aid, "quote": quote, "relation": "defines"}],
            "depends_on": [], "order": 1.0, "content": "", "content_format": "md_latex",
            "tags": [], "_status": "failed", "_errors": ["LLM 调用失败"], "status": "ai_generated"}
    monkeypatch.setattr("src.generate.engine.read_cache",
                        lambda *a, **k: {"candidates": [dict(spec)], "outline_complete": True})
    written = {}
    monkeypatch.setattr("src.generate.engine.write_cache",
                        lambda *a, **k: written.setdefault("ok", True))

    out = {"content": "补齐的内容", "content_format": "md_latex", "tags": [],
           "sources_patch": [{"anchor_id": aid, "quote": quote, "relation": "defines"}],
           "basis": "anchored"}
    dummy = DummyProvider([_resp(out)])
    cfg = {"generate": {"models": {"default": "dummy/fake"}, "fill_max_out": 12000,
                        "fill_max_in": 8000, "temperature": 0.2}}
    registry = ModelRegistry(layout)
    registry.upsert_provider({"id": "dummy", "label": "Dummy", "protocol": "openai_compat",
                              "base_url": "http://127.0.0.1:1", "api_key": None,
                              "capabilities": {"json_schema": True, "tools": True, "vision": False},
                              "models": [{"id": "fake", "context_window": 8192,
                                          "max_output_tokens": 2048, "pricing": None}]})
    client = LLMClient(registry, FakeRuntime(), cfg, overrides={"dummy": dummy})

    report = generate_chapter(doc_id, "ch01", layout=layout, client=client, config=cfg)

    assert report["cache_hit"] is True
    assert report["failures"] == [], report["failures"]
    assert dummy.calls, "缓存失败候选必须重新调用 LLM"
    blocks = [json.loads(line) for line in
              (layout.doc_dir(doc_id) / "blocks" / "ch01.jsonl").read_text(encoding="utf-8").splitlines()
              if line.strip()]
    assert any(b.get("content") == "补齐的内容" for b in blocks), "重试成功的块应落盘"
