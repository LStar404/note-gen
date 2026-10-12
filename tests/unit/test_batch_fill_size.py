"""章体量自适应（前言/小章不注水）+ 小块合批填充提速。"""

from __future__ import annotations

from src.generate.fill import fill_all
from src.generate.outline import chapter_size_plan, generate_outline


def _spec(tid: str, title: str) -> dict:
    return {"temp_id": tid, "type": "motivation", "title": title, "basis": "synthesis",
            "sources": [], "depends_on": []}


def test_chapter_size_plan_preface_and_tiny():
    """前言/小章从严控量；大章只给参考目标。"""
    target, cap, note = chapter_size_plan(120, "前言")
    assert target <= 6 and cap <= 8, (target, cap)
    assert "前言" in note
    target, cap, _ = chapter_size_plan(7, "第 10 章 小结")
    assert target == 2 and cap == 4, (target, cap)
    target, cap, _ = chapter_size_plan(30, "第 3 章")
    assert cap == 15, (target, cap)
    target, cap, _ = chapter_size_plan(338, "第 4 章 测度论")
    assert cap >= 100 and 30 <= target <= 60, (target, cap)


def test_outline_trims_overproduction_for_tiny_chapter(sample_doc):
    """小章超量出块必须截断到上限（曾 7 锚点出 6 块）。"""
    from src.core.llm import DummyProvider, LLMClient
    from src.core.model_registry import ModelRegistry
    from src.core.store import DocStore
    from src.db.runtime import RuntimeDB

    layout, doc_id = sample_doc
    registry = ModelRegistry(layout)
    registry.upsert_provider({"id": "dummy", "label": "Dummy", "protocol": "openai_compat",
                              "base_url": "http://127.0.0.1:1", "api_key": None,
                              "capabilities": {"json_schema": True, "tools": True,
                                               "vision": False},
                              "models": [{"id": "fake", "context_window": 8192,
                                          "max_output_tokens": 8192,
                                          "capabilities": {"json_schema": True, "tools": True,
                                                           "vision": False},
                                          "pricing": {"input_per_million": 0,
                                                      "output_per_million": 0,
                                                      "currency": "USD"}}]})
    cfg = {"generate": {"models": {"default": "dummy/fake", "outline": "dummy/fake"},
                        "outline_max_in": 200000, "outline_max_out": 8000,
                        "outline_batch_out": 8000, "temperature": 0.2}}
    store = DocStore(layout, doc_id)
    anchors = store.anchor_rows("ch01")
    # 模型注水：一口气回 8 个 motivation
    payload = {"blocks": [
        {"temp_id": f"t{i}", "type": "motivation", "title": f"动机{i}", "basis": "synthesis",
         "sources": [], "depends_on": [], "order": i} for i in range(8)]}
    client = LLMClient(registry, RuntimeDB(layout.runtime_db), cfg,
                       overrides={"dummy": DummyProvider([payload])})
    manifest = store.manifest()
    res = generate_outline(client, doc_id=doc_id, chapter_id="ch01", manifest=manifest,
                           anchors=anchors, model=None, config=cfg)
    _, cap, _ = chapter_size_plan(len(anchors), "")
    assert len(res.items) <= cap, f"超量出块未截断: {len(res.items)} > {cap}"


def test_fill_all_batches_ready_blocks_in_one_call():
    """fill_batch=2 时两个就绪块合成一次调用（调用数 1）。"""
    calls: list[str] = []

    class Client:
        def complete(self, task, messages, **kw):
            calls.append(task)
            return {"output": {"fills": [
                {"temp_id": "t1", "content": "甲块内容", "content_format": "md_latex",
                 "tags": [], "sources_patch": [], "basis": "synthesis"},
                {"temp_id": "t2", "content": "乙块内容", "content_format": "md_latex",
                 "tags": [], "sources_patch": [], "basis": "synthesis"},
            ]}, "raw_text": "{}", "tool_calls": [], "finish_reason": "stop",
                "usage": {"in": 1, "out": 1, "estimated": True}, "request_id": "r"}

    cfg = {"generate": {"fill_max_in": 8000, "fill_max_out": 6000, "temperature": 0.2,
                        "concurrency": 2, "fill_batch": 2}}
    filled = fill_all(Client(), doc_id="d", chapter_id="ch01",
                      outline_items=[_spec("t1", "甲"), _spec("t2", "乙")],
                      anchors_by_id={}, anchors=[], model=None, config=cfg, concurrency=2)
    assert len(calls) == 1, f"两块应合为一次调用: {calls}"
    assert set(filled) == {"t1", "t2"}, filled
    assert filled["t1"]["content"] == "甲块内容" and filled["t2"]["content"] == "乙块内容"


def test_fill_many_falls_back_to_single_for_missing_item():
    """批量输出缺块时自动回退逐块填充兜底。"""
    calls: list[str] = []

    class Client:
        def complete(self, task, messages, **kw):
            sch = kw.get("schema") or {}
            calls.append("batch" if "fills" in (sch.get("properties") or {}) else "single")
            if len(calls) == 1:
                return {"output": {"fills": [
                    {"temp_id": "t1", "content": "甲", "content_format": "md_latex",
                     "tags": [], "sources_patch": [], "basis": "synthesis"}]},
                    "raw_text": "{}", "tool_calls": [], "finish_reason": "stop",
                    "usage": {"in": 1, "out": 1, "estimated": True}, "request_id": "r"}
            return {"output": {"content": "乙补", "content_format": "md_latex", "tags": [],
                               "sources_patch": [], "basis": "synthesis"},
                    "raw_text": "{}", "tool_calls": [], "finish_reason": "stop",
                    "usage": {"in": 1, "out": 1, "estimated": True}, "request_id": "r"}

    cfg = {"generate": {"fill_max_in": 8000, "fill_max_out": 6000, "temperature": 0.2,
                        "concurrency": 2, "fill_batch": 2}}
    filled = fill_all(Client(), doc_id="d", chapter_id="ch01",
                      outline_items=[_spec("t1", "甲"), _spec("t2", "乙")],
                      anchors_by_id={}, anchors=[], model=None, config=cfg, concurrency=2)
    assert filled["t1"]["content"] == "甲" and filled["t2"]["content"] == "乙补", filled
    assert calls[0] == "batch" and calls.count("batch") == 1, calls