"""推理深度透传回归：config.reasoning_effort 必须传到模型请求体（提速关键参数）。"""

from __future__ import annotations

from src.core.config import normalize_settings
from src.core.llm import DummyProvider, LLMClient


class FakeRuntime:
    def log_usage(self, **kw):
        pass


def test_reasoning_effort_passed_to_provider():
    provider = DummyProvider([{"output": {"ok": 1}}])
    cfg = {"generate": {"models": {"default": "dummy/fake"}, "reasoning_effort": "low"}}
    client = LLMClient(_registry(), FakeRuntime(), cfg, overrides={"dummy": provider})
    client.complete("fill", [{"role": "user", "content": "hi"}], schema=None, max_tokens=100)
    assert provider.calls[0]["reasoning_effort"] == "low"


def test_reasoning_effort_omitted_when_unset():
    provider = DummyProvider([{"output": {"ok": 1}}])
    cfg = {"generate": {"models": {"default": "dummy/fake"}}}
    client = LLMClient(_registry(), FakeRuntime(), cfg, overrides={"dummy": provider})
    client.complete("fill", [{"role": "user", "content": "hi"}], schema=None, max_tokens=100)
    assert provider.calls[0]["reasoning_effort"] is None


def test_reasoning_effort_normalized():
    assert normalize_settings({"generate": {"reasoning_effort": "HIGH"}})["generate"]["reasoning_effort"] == "high"
    assert normalize_settings({"generate": {"reasoning_effort": "bogus"}})["generate"]["reasoning_effort"] is None
    assert normalize_settings({"generate": {}})["generate"]["reasoning_effort"] is None




def test_thinking_disabled_passed_to_provider():
    """思考开关透传：disabled 是输出效率的关键参数（实测省 9 倍输出 tokens）。"""
    provider = DummyProvider([{"output": {"ok": 1}}])
    cfg = {"generate": {"models": {"default": "dummy/fake"}, "thinking": "disabled"}}
    client = LLMClient(_registry(), FakeRuntime(), cfg, overrides={"dummy": provider})
    client.complete("fill", [{"role": "user", "content": "hi"}], schema=None, max_tokens=100)
    assert provider.calls[0]["thinking"] == {"type": "disabled"}


def test_thinking_omitted_when_unset():
    provider = DummyProvider([{"output": {"ok": 1}}])
    cfg = {"generate": {"models": {"default": "dummy/fake"}}}
    client = LLMClient(_registry(), FakeRuntime(), cfg, overrides={"dummy": provider})
    client.complete("fill", [{"role": "user", "content": "hi"}], schema=None, max_tokens=100)
    assert provider.calls[0]["thinking"] is None


def test_thinking_enabled_maps_to_body():
    provider = DummyProvider([{"output": {"ok": 1}}])
    cfg = {"generate": {"models": {"default": "dummy/fake"}, "thinking": "enabled"}}
    client = LLMClient(_registry(), FakeRuntime(), cfg, overrides={"dummy": provider})
    client.complete("fill", [{"role": "user", "content": "hi"}], schema=None, max_tokens=100)
    assert provider.calls[0]["thinking"] == {"type": "enabled"}


def _registry():
    from src.core.model_registry import ModelRegistry
    from src.core.paths import Layout
    import pathlib
    import tempfile

    root = pathlib.Path(tempfile.mkdtemp(prefix="ng-reg-"))
    reg = ModelRegistry(Layout(root).ensure_base())
    reg.upsert_provider({"id": "dummy", "label": "Dummy", "protocol": "openai_compat",
                         "base_url": "http://127.0.0.1:1", "api_key": None,
                         "capabilities": {"json_schema": True, "tools": True, "vision": False},
                         "models": [{"id": "fake", "context_window": 8192,
                                     "max_output_tokens": 2048, "pricing": None}]})
    return reg
