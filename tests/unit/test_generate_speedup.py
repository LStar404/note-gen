"""生成提速回归：填充连续调度（无波次屏障）+ LLM 全局在途请求总闸。"""

from __future__ import annotations

import threading
import time

from src.core.llm import LLMClient
from src.core.model_registry import ModelRegistry
from src.core.paths import Layout
from src.db.runtime import RuntimeDB
from src.generate.fill import fill_all


def _fill_resp() -> dict:
    return {"output": {"content": "内容", "content_format": "md_latex", "tags": [],
                       "sources_patch": [], "basis": "synthesis"},
            "raw_text": "{}", "tool_calls": [], "finish_reason": "stop",
            "usage": {"in": 1, "out": 1, "estimated": True}, "request_id": "r"}


def _spec(tid: str, title: str, depends_on: tuple = ()) -> dict:
    return {"temp_id": tid, "type": "motivation", "title": title, "basis": "synthesis",
            "sources": [], "depends_on": list(depends_on)}


CFG = {"generate": {"fill_max_in": 8000, "fill_max_out": 6000, "temperature": 0.2,
                    "concurrency": 2}}


def test_fill_dependent_runs_without_waiting_for_unrelated_slow_block():
    """波次屏障回归：慢块不得把已就绪的后继拖到下一波才开跑。

    旧实现按波次建池——整波（含慢块）做满才放行下一波，依赖已就绪的块
    只能干等；连续调度下 t_a 一完成 t_b 立即占坑，与 t_slow 无关。
    """
    slow_release = threading.Event()
    b_started = threading.Event()

    class Client:
        def complete(self, task, messages, **kw):
            prompt = messages[-1]["content"]
            if "SLOW" in prompt:
                assert slow_release.wait(10), "测试死锁：慢块未被释放"
            elif "TITLE-B" in prompt:
                b_started.set()
            return _fill_resp()

    items = [_spec("t_slow", "TITLE-SLOW"), _spec("t_a", "TITLE-A"),
             _spec("t_b", "TITLE-B", depends_on=("t_a",))]
    result: dict[str, dict] = {}

    def worker() -> None:
        result.update(fill_all(Client(), doc_id="d", chapter_id="ch01", outline_items=items,
                               anchors_by_id={}, anchors=[], model=None,
                               config=CFG, concurrency=2))

    th = threading.Thread(target=worker, daemon=True)
    th.start()
    # 慢块仍阻塞时，依赖已就绪的 t_b 必须已开跑（旧波次实现会在此超时）
    assert b_started.wait(5), "depends_on 目标完成后应立即开跑，不得等无关慢块"
    slow_release.set()
    th.join(20)
    assert not th.is_alive()
    assert set(result) == {"t_slow", "t_a", "t_b"}
    assert all(v["_status"] == "ok" for v in result.values()), result


def test_fill_all_still_orders_depends_on_target_first():
    """提速不得破坏 5.3.1 语义：depends_on 目标必须先于依赖者完成。"""
    order: list[str] = []
    lock = threading.Lock()

    class Client:
        def complete(self, task, messages, **kw):
            prompt = messages[-1]["content"]
            tid = "t_b" if "TITLE-B" in prompt else "t_a"
            with lock:
                if tid == "t_b":
                    assert "t_a" in order, f"依赖目标未先完成: {order}"
                order.append(tid)
            return _fill_resp()

    items = [_spec("t_b", "TITLE-B", depends_on=("t_a",)), _spec("t_a", "TITLE-A")]
    filled = fill_all(Client(), doc_id="d", chapter_id="ch01", outline_items=items,
                      anchors_by_id={}, anchors=[], model=None, config=CFG, concurrency=2)
    assert set(filled) == {"t_a", "t_b"}
    assert order[0] == "t_a", order


def test_llm_client_caps_inflight_requests(tmp_path):
    """全局在途请求总闸：并行分章叠起来的并发也不得超过 max_concurrent_requests。"""
    layout = Layout(tmp_path).ensure_base()
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

    class CountingProvider:
        def __init__(self) -> None:
            self.active = 0
            self.peak = 0
            self._lock = threading.Lock()

        def complete(self, model, messages, schema, max_tokens, **kw):
            with self._lock:
                self.active += 1
                self.peak = max(self.peak, self.active)
            time.sleep(0.1)
            with self._lock:
                self.active -= 1
            return {"output": {}, "raw_text": "{}", "tool_calls": [], "finish_reason": "stop",
                    "usage": {"in": 1, "out": 1, "estimated": True}, "request_id": "r"}

    provider = CountingProvider()
    cfg = {"generate": {"models": {"default": "dummy/fake"},
                        "max_concurrent_requests": 2, "timeout": 30}}
    client = LLMClient(registry, RuntimeDB(layout.runtime_db), cfg,
                       overrides={"dummy": provider})

    def call() -> None:
        client.complete("fill", [{"role": "user", "content": "hi"}], schema=None, max_tokens=8)

    threads = [threading.Thread(target=call) for _ in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(30)
    assert not any(th.is_alive() for th in threads)
    assert provider.peak <= 2, f"在途请求峰值 {provider.peak} 超过总闸 2"
    assert provider.peak == 2, f"总闸不应把并发钉成串行: peak={provider.peak}"


def test_llm_client_stats_track_calls_retries_and_time(tmp_path):
    """调用遥测：calls/retries/耗时/排队时长必须可统计（报告 timing.llm 的数据源）。"""
    from src.core.errors import RetryableError
    from src.core.llm import DummyProvider

    layout = Layout(tmp_path).ensure_base()
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
    cfg = {"generate": {"models": {"default": "dummy/fake"},
                        "max_concurrent_requests": 2, "timeout": 30}}
    provider = DummyProvider([RetryableError("瞬时故障"), {"output": {"ok": 1}}])
    client = LLMClient(registry, RuntimeDB(layout.runtime_db), cfg,
                       overrides={"dummy": provider})
    client.complete("fill", [{"role": "user", "content": "hi"}], schema=None, max_tokens=8)
    s = client.stats
    assert s["calls"] == 1, s
    assert s["retries"] == 1, s
    assert s["call_seconds"] >= 0 and s["wait_seconds"] >= 0, s
    assert s["tokens_in"] > 0 and s["tokens_out"] > 0, s


def test_429_throttles_concurrency_and_recovers(tmp_path, monkeypatch):
    """并发类 429：有效并发砍半、成功逐格回升（上游额度随余额档位漂移，实测 429 风暴）。"""
    from src.core.errors import RetryableError
    from src.core.llm import DummyProvider

    monkeypatch.setattr("src.core.llm.time.sleep", lambda s: None)   # 退避不真等
    layout = Layout(tmp_path).ensure_base()
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
    cfg = {"generate": {"models": {"default": "dummy/fake"},
                        "max_concurrent_requests": 8, "timeout": 30}}
    boom = RetryableError("LLM 服务返回 429：concurrency limit", detail={"status": 429})
    provider = DummyProvider([boom, {"output": {"ok": 1}}, {"output": {"ok": 2}}])
    client = LLMClient(registry, RuntimeDB(layout.runtime_db), cfg,
                       overrides={"dummy": provider})
    assert client._limit == 8
    client.complete("fill", [{"role": "user", "content": "hi"}], schema=None, max_tokens=8)
    # 第一发 429 砍半 8→4，重试成功回升 4→5
    assert client._limit == 5, f"429 砍半/成功回升失灵: {client._limit}"
    client.complete("fill", [{"role": "user", "content": "hi"}], schema=None, max_tokens=8)
    assert client._limit == 6, f"成功应回升一格: {client._limit}"


def test_non_429_retryable_does_not_throttle(tmp_path, monkeypatch):
    """5xx/传输类瞬态错误不动并发闸（那不是并发问题）。"""
    from src.core.errors import RetryableError
    from src.core.llm import DummyProvider

    monkeypatch.setattr("src.core.llm.time.sleep", lambda s: None)
    layout = Layout(tmp_path).ensure_base()
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
    cfg = {"generate": {"models": {"default": "dummy/fake"},
                        "max_concurrent_requests": 8, "timeout": 30}}
    boom = RetryableError("LLM 服务返回 503", detail={"status": 503})
    provider = DummyProvider([boom, {"output": {"ok": 1}}])
    client = LLMClient(registry, RuntimeDB(layout.runtime_db), cfg,
                       overrides={"dummy": provider})
    client.complete("fill", [{"role": "user", "content": "hi"}], schema=None, max_tokens=8)
    assert client._limit == 8, f"5xx 不应动闸: {client._limit}"


def test_generate_report_contains_timing(sample_doc):
    """生成报告必须带 timing：总耗时/生成墙钟/提交耗时/LLM 遥测——「太慢」要能定位。"""
    from src.core.llm import DummyProvider
    from src.core.store import DocStore
    from src.generate.engine import generate_chapter

    layout, doc_id = sample_doc
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
    cfg = {"generate": {"models": {"default": "dummy/fake", "outline": "dummy/fake",
                                   "fill": "dummy/fake"},
                        "concurrency": 2, "temperature": 0.2, "max_cost_per_chapter": 0.0},
           "cache": {"dir": "cache/gen"}}
    store = DocStore(layout, doc_id)
    anchors = store.anchor_rows("ch01")
    aid = anchors[1]["anchor_id"]
    quote = anchors[1]["content"][:20]
    outline = {"blocks": [
        {"temp_id": "t1", "type": "definition", "title": "定义一", "basis": "anchored",
         "sources": [{"anchor_id": aid, "quote": quote, "relation": "defines"}],
         "depends_on": [], "order": 1}]}
    fill1 = {"content": "内容 $x$。", "content_format": "md_latex", "tags": [],
             "sources_patch": [{"anchor_id": aid, "quote": quote, "relation": "defines"}],
             "basis": "anchored"}
    client = LLMClient(registry, RuntimeDB(layout.runtime_db), cfg,
                       overrides={"dummy": DummyProvider([outline, fill1])})
    report = generate_chapter(doc_id, "ch01", layout=layout, client=client, config=cfg,
                              registry=registry, force=True, use_cache=False)
    t = report.get("timing") or {}
    assert "total_s" in t and "gen_s" in t and "commit_s" in t, t
    assert isinstance(t.get("llm"), dict) and t["llm"].get("calls", 0) >= 2, t
    assert t["total_s"] >= 0
