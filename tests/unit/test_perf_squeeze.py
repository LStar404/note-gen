"""性能压探单元测试：空 sources 提示规则、合批纠正重试、提纲分批上限、连接池复用、分派并发。"""

from __future__ import annotations

import re

import src.generate.engine as engine
from src.core.llm import DummyProvider, LLMClient, OpenAICompatAdapter
from src.core.model_registry import ModelRegistry
from src.core.store import DocStore
from src.db.runtime import RuntimeDB
from src.generate.fill import fill_all
from src.generate.outline import chapter_size_plan, generate_outline
from src.generate.prompts import FILL_MANY_PROMPT, FILL_PROMPT, OUTLINE_PROMPT


def _resp(payload: dict) -> dict:
    return {"output": payload, "raw_text": "{}", "tool_calls": [], "finish_reason": "stop",
            "usage": {"in": 1, "out": 1, "estimated": True}, "request_id": "r"}


def _spec(tid: str, title: str) -> dict:
    return {"temp_id": tid, "type": "motivation", "title": title, "basis": "synthesis",
            "sources": [], "depends_on": []}


def _good(tid: str, text: str) -> dict:
    return {"temp_id": tid, "content": text, "content_format": "md_latex", "tags": [],
            "sources_patch": [], "basis": "synthesis"}


def _bad(tid: str) -> dict:
    # 编造锚点：spec 未提供任何来源 → ANCHOR_NOT_FOUND（69f 前言章返工风暴的形状）
    return {"temp_id": tid, "content": "甲", "content_format": "md_latex", "tags": [],
            "sources_patch": [{"anchor_id": "a_fake", "quote": "q", "relation": "summarizes"}],
            "basis": "synthesis"}


CFG_FILL = {"generate": {"fill_max_in": 8000, "fill_max_out": 6000, "temperature": 0.2,
                         "concurrency": 2, "fill_batch": 2}}


def test_fill_prompts_rule_empty_sources():
    """link/空 sources 块必须被提示词明令 sources_patch=[]（曾因无说明被编造来源拒单返工）。"""
    for p in (FILL_PROMPT, FILL_MANY_PROMPT):
        assert "link" in p and "sources_patch 返回 []" in p, "缺空 sources 规则"
    assert "本批只处理" in OUTLINE_PROMPT and "{batch_cap}" in OUTLINE_PROMPT


def test_fill_many_corrective_batch_retry_before_single_storm():
    """整批被拒先整批纠正（仍一次调用伺候 N 块），不得直接逐块返工。"""
    calls: list[str] = []

    class Client:
        def complete(self, task, messages, **kw):
            sch = kw.get("schema") or {}
            calls.append("batch" if "fills" in (sch.get("properties") or {}) else "single")
            if len(calls) == 1:
                return _resp({"fills": [_bad("t1"), _bad("t2")]})
            return _resp({"fills": [_good("t1", "甲修"), _good("t2", "乙修")]})

    filled = fill_all(Client(), doc_id="d", chapter_id="ch01",
                      outline_items=[_spec("t1", "甲"), _spec("t2", "乙")],
                      anchors_by_id={}, anchors=[], model=None,
                      config=CFG_FILL, concurrency=2)
    assert calls == ["batch", "batch"], f"应整批纠正而非逐块返工: {calls}"
    assert filled["t1"]["content"] == "甲修" and filled["t2"]["content"] == "乙修", filled
    assert filled["t1"]["_status"] == "ok" and filled["t2"]["_status"] == "ok"


def test_fill_many_corrective_retry_still_fails_falls_back():
    """纠正一轮仍不过的块才逐块兜底（兜底是最后防线，不得省）。"""
    calls: list[str] = []

    class Client:
        def complete(self, task, messages, **kw):
            sch = kw.get("schema") or {}
            calls.append("batch" if "fills" in (sch.get("properties") or {}) else "single")
            if calls[-1] == "batch" and len(calls) == 1:
                return _resp({"fills": [_bad("t1"), _bad("t2")]})
            if calls[-1] == "batch":
                return _resp({"fills": [_bad("t1"), _bad("t2")]})   # 纠正仍失败
            return _resp(_good("t1", "甲兜") if len(calls) == 3 else _good("t2", "乙兜"))

    filled = fill_all(Client(), doc_id="d", chapter_id="ch01",
                      outline_items=[_spec("t1", "甲"), _spec("t2", "乙")],
                      anchors_by_id={}, anchors=[], model=None,
                      config=CFG_FILL, concurrency=2)
    assert calls.count("batch") == 2, f"纠正只做一轮: {calls}"
    assert calls.count("single") == 2, f"剩余块逐块兜底: {calls}"
    assert filled["t1"]["content"] in ("甲兜", "乙兜") and filled["t2"]["content"] in ("甲兜", "乙兜")


def test_fill_many_single_reject_goes_straight_to_single():
    """仅 1 块被拒不值得整批纠正：直接逐块兜底（1 次调用）。"""
    calls: list[str] = []

    class Client:
        def complete(self, task, messages, **kw):
            sch = kw.get("schema") or {}
            calls.append("batch" if "fills" in (sch.get("properties") or {}) else "single")
            if calls[-1] == "batch":
                return _resp({"fills": [_good("t1", "甲"), _bad("t2")]})
            return _resp(_good("t2", "乙补"))

    filled = fill_all(Client(), doc_id="d", chapter_id="ch01",
                      outline_items=[_spec("t1", "甲"), _spec("t2", "乙")],
                      anchors_by_id={}, anchors=[], model=None,
                      config=CFG_FILL, concurrency=2)
    assert calls == ["batch", "single"], calls
    assert filled["t1"]["content"] == "甲" and filled["t2"]["content"] == "乙补", filled


def test_fill_many_shrinks_sources_when_over_input_budget():
    """批量提示词超 fill_max_in 时裁剪来源原文（与 fill_one 护栏一致）。"""
    prompts: list[str] = []
    anchor = {"anchor_id": "a1", "content": "原" * 1000, "page": 1, "reading_order": 0}

    class Client:
        def complete(self, task, messages, **kw):
            prompts.append(messages[-1]["content"])
            return _resp({"fills": [
                {"temp_id": "t1", "content": "正文", "content_format": "md_latex", "tags": [],
                 "sources_patch": [{"anchor_id": "a1", "quote": "原" * 20,
                                    "relation": "defines"}],
                 "basis": "anchored"},
                {"temp_id": "t2", "content": "动机", "content_format": "md_latex", "tags": [],
                 "sources_patch": [], "basis": "synthesis"}]})

    spec = {"temp_id": "t1", "type": "definition", "title": "定义", "basis": "anchored",
            "sources": [{"anchor_id": "a1", "quote": "原" * 20, "relation": "defines"}],
            "depends_on": []}
    cfg = {"generate": {"fill_max_in": 50, "fill_max_out": 6000, "temperature": 0.2,
                        "fill_batch": 2}}
    filled = fill_all(Client(), doc_id="d", chapter_id="ch01",
                      outline_items=[spec, _spec("t2", "动机")],
                      anchors_by_id={"a1": anchor}, anchors=[anchor], model=None,
                      config=cfg, concurrency=1)
    assert filled["t1"]["_status"] == "ok"
    assert ("原" * 600) in prompts[0] and ("原" * 800) not in prompts[0], "超预算应裁剪到 600 字"


def test_outline_prompt_carries_batch_cap():
    """分批硬上限必须进提示词（止「生成后剪枝」白烧输出 tokens）。"""
    prompts: list[str] = []

    class Client:
        def complete(self, task, messages, **kw):
            prompts.append(messages[-1]["content"])
            aid = sorted(set(re.findall(r"a_ns_[0-9a-f]+", prompts[-1])))[0]
            return _resp({"blocks": [{
                "temp_id": "t1", "type": "definition", "title": "定义", "basis": "anchored",
                "sources": [{"anchor_id": aid, "quote": "原文", "relation": "defines"}],
                "depends_on": [], "order": 1}]})

    anchors = []
    for i in range(3):
        text = f"ANCH{i} 本段原文。" + "原" * 100
        anchors.append({"anchor_id": f"a_ns_{i:016d}", "doc_id": "doc-x", "chapter_id": "ch01",
                        "page": i + 1, "reading_order": 0, "content": text, "text": text,
                        "content_type": "paragraph"})
    cfg = {"generate": {"outline_max_in": 10 ** 9, "outline_max_out": 10 ** 9,
                        "concurrency": 1, "temperature": 0.2, "output_language": "zh"}}
    res = generate_outline(Client(), doc_id="doc-x", chapter_id="ch01",
                           manifest={"profile": "textbook", "title": "书", "chapters": []},
                           anchors=anchors, model=None, config=cfg)
    assert res.items, res
    _, cap, _ = chapter_size_plan(len(anchors), "")
    assert prompts and f"最多输出 {cap} 个条目" in prompts[0], prompts[0][:400]


def test_llm_adapter_reuses_connection_pool():
    """持久 httpx.Client：跨调用复用连接池，不再每调用新建连接。"""
    httpx = __import__("httpx")
    seen: list = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"id": "x", "choices": [
            {"message": {"content": '{"ok": 1}'}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 5}})

    adapter = OpenAICompatAdapter("p", "http://llm.test", "key",
                                  transport=httpx.MockTransport(handler))
    for _ in range(2):
        out = adapter.complete("m", [{"role": "user", "content": "hi"}], None, 8)
        assert out["output"] == {"ok": 1}
    assert len(seen) == 2
    assert adapter._get_client() is adapter._get_client(), "Client 必须复用"
    assert seen[0].headers["Authorization"] == "Bearer key"
    adapter.close()


def test_engine_fill_pool_matches_concurrency(sample_doc, monkeypatch):
    """外层分派池不得钳在 4：必须按 generate.concurrency 开闸。"""
    layout, doc_id = sample_doc
    registry = ModelRegistry(layout)
    registry.upsert_provider({"id": "dummy", "label": "Dummy", "protocol": "openai_compat",
                              "base_url": "http://127.0.0.1:1", "api_key": None,
                              "capabilities": {"json_schema": True, "tools": True, "vision": False},
                              "models": [{"id": "fake", "context_window": 8192,
                                          "max_output_tokens": 2048,
                                          "capabilities": {"json_schema": True, "tools": True,
                                                           "vision": False},
                                          "pricing": {"input_per_million": 0,
                                                      "output_per_million": 0,
                                                      "currency": "USD"}}]})
    cfg = {"generate": {"models": {"default": "dummy/fake", "outline": "dummy/fake",
                                   "fill": "dummy/fake"},
                        "concurrency": 7, "fill_batch": 2, "temperature": 0.2,
                        "max_cost_per_chapter": 0.0, "max_concurrent_requests": 16,
                        "timeout": 30},
           "cache": {"dir": "cache/gen"}}
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
    # 不带 temp_id → 位置对齐兜底（引擎会给 temp_id 加 b0- 批次前缀，测试不赌前缀形状）
    fills = {"fills": [
        {"content": "第一块内容。", "content_format": "md_latex", "tags": [],
         "sources_patch": [{"anchor_id": aid, "quote": quote, "relation": "defines"}],
         "basis": "anchored"},
        {"content": "第二块内容。", "content_format": "md_latex", "tags": [],
         "sources_patch": [], "basis": "synthesis"}]}
    recorded: dict = {}
    real_pool = engine.ThreadPoolExecutor

    class RecPool(real_pool):
        def __init__(self, *a, **kw):
            recorded["max_workers"] = kw.get("max_workers")
            super().__init__(*a, **kw)

    monkeypatch.setattr(engine, "ThreadPoolExecutor", RecPool)
    client = LLMClient(registry, RuntimeDB(layout.runtime_db), cfg,
                       overrides={"dummy": DummyProvider([outline, fills])})
    r = engine.generate_chapter(doc_id, "ch01", layout=layout, client=client, config=cfg,
                                registry=registry, force=True)
    assert r["status"] == "complete", r
    assert recorded.get("max_workers") == 7, f"外层分派池应按 concurrency 开闸: {recorded}"
