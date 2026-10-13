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




def test_thinking_normalized():
    """thinking 拼错不得静默放行（否则悄悄回到昂贵的思考默认）。"""
    assert normalize_settings({"generate": {"thinking": "DISABLED"}})["generate"]["thinking"] == "disabled"
    assert normalize_settings({"generate": {"thinking": "bogus"}})["generate"]["thinking"] is None
    assert normalize_settings({"generate": {}})["generate"]["thinking"] is None


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


def test_settings_hot_apply_to_live_client(workspace):
    """设置保存后必须热应用到存活客户端：thinking/推理深度/超时/并发上限曾只换
    config 引用、字段仍是构造时旧值——UI 改完设置一直不生效，只能重启服务。"""
    from fastapi.testclient import TestClient

    from src.api.main import create_app

    app = create_app(workspace.root)
    with TestClient(app, base_url="http://127.0.0.1:8787") as client:
        headers = {"X-Local-Token": app.state.app_state.token_value}
        r = client.put("/api/v1/settings", json={"generate": {
            "thinking": "enabled", "reasoning_effort": "high",
            "timeout": 77, "max_concurrent_requests": 5}}, headers=headers)
        assert r.status_code == 200, r.text
        lc = app.state.app_state.client
        assert lc.thinking == {"type": "enabled"}, lc.thinking
        assert lc.reasoning_effort == "high", lc.reasoning_effort
        assert lc.timeout == 77.0, lc.timeout
        assert lc._max_slots == 5, lc._max_slots
        # 再改回同样立即生效（对象原地热应用，运行中任务共用）
        r2 = client.put("/api/v1/settings", json={"generate": {
            "thinking": "disabled", "max_concurrent_requests": 9}}, headers=headers)
        assert r2.status_code == 200, r2.text
        lc2 = app.state.app_state.client
        assert lc2 is lc, "热应用不得换掉客户端对象"
        assert lc2.thinking == {"type": "disabled"}
        assert lc2._max_slots == 9
