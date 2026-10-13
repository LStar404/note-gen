"""LLM 上游错误分类（9.5 / 10.4）：401/403 与其余 4xx 不可重试，仅 429/5xx 重试。

回归背景：无效密钥（401）曾被归入 RetryableError，白等 3 次退避后只报「LLM 调用被拒绝 401」，
用户无法从提示里看出「密钥本身无效」这一唯一结论。
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from src.api.main import create_app
from src.core.errors import AuthError, LLMError, RetryableError
from src.core.llm import OpenAICompatAdapter

MSGS = [{"role": "user", "content": "ping"}]


class FakeResp:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = "{}"):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self) -> dict:
        return self._payload


def _adapter(monkeypatch, *, resp=None, exc: Exception | None = None,
             calls: list | None = None) -> OpenAICompatAdapter:
    def fake_post(url, json=None, headers=None, timeout=None):
        if calls is not None:
            calls.append(url)
        if exc is not None:
            raise exc
        return resp

    # 桩点在持久连接池上（连接复用后请求经 Client.post 发出，不再是 httpx.post）
    monkeypatch.setattr(OpenAICompatAdapter, "_get_client",
                        lambda self: SimpleNamespace(post=fake_post))
    return OpenAICompatAdapter("deepseek", "https://api.deepseek.com", "sk-x")


@pytest.mark.parametrize("status", [401, 403])
def test_auth_status_is_not_retryable(monkeypatch, status):
    a = _adapter(monkeypatch, resp=FakeResp(
        status, text='{"error":{"message":"Authentication Fails, Your api key: ****b71e is invalid"}}'))
    with pytest.raises(AuthError) as ei:
        a.complete("deepseek-chat", MSGS, None, 8)

    assert ei.value.code == "LLM_AUTH_FAILED"
    assert not isinstance(ei.value, RetryableError)
    assert "鉴权失败" in str(ei.value) and "deepseek-chat" in str(ei.value)
    assert ei.value.detail["status"] == status
    assert "Authentication Fails" in ei.value.detail["body"]


@pytest.mark.parametrize("status", [400, 404, 422])
def test_other_4xx_not_retryable(monkeypatch, status):
    a = _adapter(monkeypatch, resp=FakeResp(status, text='{"error":"bad request"}'))
    with pytest.raises(LLMError) as ei:
        a.complete("some-model", MSGS, None, 8)
    assert ei.value.code == "LLM_ERROR"
    assert not isinstance(ei.value, RetryableError)
    assert not isinstance(ei.value, AuthError)
    assert ei.value.detail["status"] == status


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_transient_status_still_retryable(monkeypatch, status):
    a = _adapter(monkeypatch, resp=FakeResp(status, text="busy"))
    with pytest.raises(RetryableError):
        a.complete("some-model", MSGS, None, 8)


def test_transport_error_still_retryable(monkeypatch):
    a = _adapter(monkeypatch, exc=httpx.ConnectError("connection refused"))
    with pytest.raises(RetryableError) as ei:
        a.complete("some-model", MSGS, None, 8)
    assert "LLM 请求失败" in str(ei.value)


def test_retryable_message_carries_upstream(monkeypatch):
    """瞬态失败必须带上游原话（只报状态码的失败原因诊断价值为零）。"""
    a = _adapter(monkeypatch, resp=FakeResp(503, text='{"error":"upstream busy"}'))
    with pytest.raises(RetryableError) as ei:
        a.complete("some-model", MSGS, None, 8)
    assert "upstream busy" in str(ei.value), str(ei.value)
    assert ei.value.detail["status"] == 503


def test_success_path_unchanged(monkeypatch):
    payload = {"id": "chatcmpl-1", "choices": [{"message": {"content": '{"a": 1}'},
                                                "finish_reason": "stop"}],
               "usage": {"prompt_tokens": 3, "completion_tokens": 2}}
    a = _adapter(monkeypatch, resp=FakeResp(200, payload=payload))
    out = a.complete("some-model", MSGS, None, 8)
    assert out["output"] == {"a": 1} and out["usage"]["in"] == 3 and out["usage"]["out"] == 2


class StubAdapter:
    """只抛固定异常并计数，用于验证 LLMClient 的重试策略。"""

    def __init__(self, exc: Exception):
        self.exc = exc
        self.calls = 0

    def complete(self, *args, **kwargs):
        self.calls += 1
        raise self.exc


@pytest.fixture()
def app_state(workspace):
    app = create_app(workspace.root)
    token = app.state.app_state.token_value
    with TestClient(app, base_url="http://127.0.0.1:8787") as c:
        c.headers.update({"X-Local-Token": token})
        assert c.put("/api/v1/models/openai", json={
            "id": "openai", "label": "OpenAI", "protocol": "openai_compat",
            "base_url": "https://api.openai.com/v1", "api_key": "sk-test-1234",
            "api_key_env": None,
            "models": [{"id": "gpt-4o-mini",
                        "capabilities": {"json_schema": True, "tools": True, "vision": False},
                        "pricing": {"input_per_million": 0.15, "output_per_million": 0.6}}],
        }).status_code == 200
        assert c.put("/api/v1/model-routes", json={"default": "openai/gpt-4o-mini"}).status_code == 200
        yield app.state.app_state


def test_client_does_not_retry_auth_error(monkeypatch, app_state):
    monkeypatch.setattr("src.core.llm.time.sleep", lambda _s: None)
    stub = StubAdapter(AuthError("LLM 鉴权失败 401", code="LLM_AUTH_FAILED"))
    app_state.client.overrides["openai"] = stub

    with pytest.raises(AuthError):
        app_state.client.complete("default", MSGS, max_tokens=8)
    assert stub.calls == 1, "鉴权失败不得重试"


def test_client_retries_transient_error_three_times(monkeypatch, app_state):
    monkeypatch.setattr("src.core.llm.time.sleep", lambda _s: None)
    stub = StubAdapter(RetryableError("LLM 服务返回 429"))
    app_state.client.overrides["openai"] = stub

    with pytest.raises(RetryableError):
        app_state.client.complete("default", MSGS, max_tokens=8)
    assert stub.calls == 3, "瞬态错误应保留 3 次尝试"


class CapturingAdapter:
    """记录收到的 model 参数，用于验证传给供应商的是裸模型 id。"""

    def __init__(self):
        self.seen: list[str] = []

    def complete(self, model, messages, schema, max_tokens, **kwargs):
        self.seen.append(model)
        return {"output": {"ok": True}, "usage": {"in": 1, "out": 1},
                "request_id": "req_stub", "finish_reason": "stop"}


def test_adapter_receives_bare_model_id_not_route_name(app_state):
    cap = CapturingAdapter()
    app_state.client.overrides["openai"] = cap

    out = app_state.client.complete("default", MSGS, max_tokens=8)

    # 路由全名 openai/gpt-4o-mini 只用于本地记账，不能出现在供应商 API 的 model 字段
    assert cap.seen == ["gpt-4o-mini"], "路由全名被原样传给了供应商 API"
    assert out["model"] == "openai/gpt-4o-mini" and out["provider_id"] == "openai"
