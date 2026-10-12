"""配额中断回归：半路 token 耗尽 = 暂停落盘，下次生成续填并复用已完成块。"""

from __future__ import annotations

from src.core.errors import QuotaExhausted
from src.core.llm import DummyProvider, LLMClient
from src.core.model_registry import ModelRegistry
from src.core.paths import Layout
from src.core.store import DocStore
from src.db.runtime import RuntimeDB
from src.generate.engine import generate_chapter

CFG = {"generate": {"models": {"default": "dummy/fake", "outline": "dummy/fake",
                               "fill": "dummy/fake"},
                    "concurrency": 1, "temperature": 0.2, "max_cost_per_chapter": 0.0,
                    "max_concurrent_requests": 2, "timeout": 30},
       "cache": {"dir": "cache/gen"}}


def _provider_for(layout: Layout) -> ModelRegistry:
    registry = ModelRegistry(layout)
    registry.upsert_provider({"id": "dummy", "label": "Dummy", "protocol": "openai_compat",
                              "base_url": "http://127.0.0.1:1", "api_key": None,
                              "capabilities": {"json_schema": True, "tools": True,
                                               "vision": False},
                              "models": [{"id": "fake", "context_window": 8192,
                                          "max_output_tokens": 2048,
                                          "capabilities": {"json_schema": True, "tools": True,
                                                           "vision": False},
                                          "pricing": {"input_per_million": 0,
                                                      "output_per_million": 0,
                                                      "currency": "USD"}}]})
    return registry


def test_quota_exhaustion_pauses_then_next_run_resumes(sample_doc):
    """半路配额耗尽：报告 paused + 缓存落盘；下次生成只补失败块、复用已完成块。"""
    layout, doc_id = sample_doc
    registry = _provider_for(layout)
    store = DocStore(layout, doc_id)
    anchors = store.anchor_rows("ch01")
    aid = anchors[1]["anchor_id"]
    quote = anchors[1]["content"][:20]
    outline = {"blocks": [
        {"temp_id": "t1", "type": "definition", "title": "定义一", "basis": "anchored",
         "sources": [{"anchor_id": aid, "quote": quote, "relation": "defines"}],
         "depends_on": [], "order": 1},
        {"temp_id": "t2", "type": "motivation", "title": "动机二", "basis": "synthesis",
         "sources": [], "depends_on": [], "order": 2}]}
    fill1 = {"content": "第一块内容。", "content_format": "md_latex", "tags": [],
             "sources_patch": [{"anchor_id": aid, "quote": quote, "relation": "defines"}],
             "basis": "anchored"}
    fill2 = {"content": "第二块内容。", "content_format": "md_latex", "tags": [],
             "sources_patch": [], "basis": "synthesis"}

    # 第一次：t1 成功，t2 配额耗尽
    client1 = LLMClient(registry, RuntimeDB(layout.runtime_db), CFG,
                        overrides={"dummy": DummyProvider([outline, fill1,
                                                           QuotaExhausted("配额已用尽")])})
    r1 = generate_chapter(doc_id, "ch01", layout=layout, client=client1, config=CFG,
                          registry=registry, force=True, use_cache=True)
    assert r1["status"] == "paused", r1
    assert r1.get("resume_hint"), r1
    assert client1.halted_reason, "配额熔断应已置位"
    blocks1 = {b["title"] for b in store.block_rows("ch01")}
    assert "定义一" in blocks1, f"已完成块应已落库: {blocks1}"

    # 第二次：缓存命中，只补 t2（新 Key/充值后的恢复调用）
    client2 = LLMClient(registry, RuntimeDB(layout.runtime_db), CFG,
                        overrides={"dummy": DummyProvider([fill2])})
    r2 = generate_chapter(doc_id, "ch01", layout=layout, client=client2, config=CFG,
                          registry=registry)
    assert r2["cache_hit"] is True, r2
    assert r2["status"] == "complete", r2
    blocks2 = {b["title"] for b in store.block_rows("ch01")}
    assert {"定义一", "动机二"} <= blocks2, f"续填后两块都应存在: {blocks2}"
    assert len(client2.calls if hasattr(client2, "calls") else []) >= 0


def test_quota_circuit_breaker_stops_further_calls(tmp_path):
    """熔断：配额耗尽后本次任务内不再发请求（剩余块快速失败，不打爆 API）。"""
    layout = Layout(tmp_path).ensure_base()
    registry = _provider_for(layout)
    provider = DummyProvider([QuotaExhausted("配额已用尽"), {"output": {"x": 1}}])
    client = LLMClient(registry, RuntimeDB(layout.runtime_db), CFG,
                       overrides={"dummy": provider})
    try:
        client.complete("fill", [{"role": "user", "content": "hi"}], schema=None, max_tokens=8)
        raise AssertionError("应抛 QuotaExhausted")
    except QuotaExhausted:
        pass
    n_calls = len(provider.calls)
    try:
        client.complete("fill", [{"role": "user", "content": "again"}], schema=None, max_tokens=8)
        raise AssertionError("应抛 QuotaExhausted")
    except QuotaExhausted:
        pass
    assert len(provider.calls) == n_calls, "熔断后不得再打上游 API"


def test_resume_reuses_ok_blocks_across_cache_key_change(sample_doc):
    """续跑语义：换模型/换参数（缓存键失配）也必须复用已完成块、只补缺口。
    曾经 115/117 已完成却整章重烧。"""
    layout, doc_id = sample_doc
    registry = _provider_for(layout)
    store = DocStore(layout, doc_id)
    anchors = store.anchor_rows("ch01")
    aid = anchors[1]["anchor_id"]
    quote = anchors[1]["content"][:20]
    outline = {"blocks": [
        {"temp_id": "t1", "type": "definition", "title": "定义一", "basis": "anchored",
         "sources": [{"anchor_id": aid, "quote": quote, "relation": "defines"}],
         "depends_on": [], "order": 1},
        {"temp_id": "t2", "type": "motivation", "title": "动机二", "basis": "synthesis",
         "sources": [], "depends_on": [], "order": 2}]}
    fill1 = {"content": "第一块内容。", "content_format": "md_latex", "tags": [],
             "sources_patch": [{"anchor_id": aid, "quote": quote, "relation": "defines"}],
             "basis": "anchored"}
    fill2 = {"content": "第二块内容。", "content_format": "md_latex", "tags": [],
             "sources_patch": [], "basis": "synthesis"}

    # 第一次：t1 成功、t2 配额中断（断点快照落缓存）
    client1 = LLMClient(registry, RuntimeDB(layout.runtime_db), CFG,
                        overrides={"dummy": DummyProvider([outline, fill1,
                                                           QuotaExhausted("配额已用尽")])})
    r1 = generate_chapter(doc_id, "ch01", layout=layout, client=client1, config=CFG,
                          registry=registry, force=True, use_cache=True)
    assert r1["status"] == "paused", r1

    # 第二次：温度不同 → 缓存键不同（模拟换模型/升提示词），resume=True 仍须复用
    cfg2 = {**CFG, "generate": {**CFG["generate"], "temperature": 0.9}}
    client2 = LLMClient(registry, RuntimeDB(layout.runtime_db), cfg2,
                        overrides={"dummy": DummyProvider([fill2])})
    r2 = generate_chapter(doc_id, "ch01", layout=layout, client=client2, config=cfg2,
                          registry=registry, resume=True)
    assert r2.get("resumed") is True, r2
    blocks = {b["title"]: b for b in store.block_rows("ch01")}
    assert "定义一" in blocks and "动机二" in blocks, list(blocks)
    assert blocks["定义一"]["content"] == "第一块内容。", "已完成块必须原样复用（不得重填）"

