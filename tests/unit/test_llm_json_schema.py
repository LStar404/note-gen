"""能力标志驱动行为（9.5.1）：模型 json_schema=false 时不得发 json_schema response_format。

回归背景：DeepSeek 对 response_format.type=json_schema 回 400
"This response_format type is unavailable now"；而能力标志此前只出现在 test_connection 的
提示文案里，调用路径从不检查，所以密钥与路由配好后「生成本章」依旧失败。
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from src.api.main import create_app
from src.core.llm import OpenAICompatAdapter

MSGS = [{"role": "user", "content": "给出结果"}]
SCHEMA = {"type": "object", "properties": {"a": {"type": "integer"}}, "required": ["a"]}


class FakeResp:
    status_code = 200
    text = "{}"

    def json(self) -> dict:
        return {"id": "c1",
                "choices": [{"message": {"content": '{"a": 1}'}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3}}


@pytest.fixture()
def bodies(monkeypatch):
    sent: list[dict] = []

    def fake_post(url, json=None, headers=None, timeout=None):
        sent.append(json)
        return FakeResp()

    monkeypatch.setattr(httpx, "post", fake_post)
    return sent


def _adapter() -> OpenAICompatAdapter:
    return OpenAICompatAdapter("deepseek", "https://api.deepseek.com", "sk-x")


def test_json_schema_true_sends_json_schema(bodies):
    out = _adapter().complete("deepseek-flash", MSGS, SCHEMA, 16, json_schema=True)

    assert out["output"] == {"a": 1}
    rf = bodies[0]["response_format"]
    assert rf["type"] == "json_schema" and rf["json_schema"]["schema"] == SCHEMA
    assert bodies[0]["messages"] == MSGS                     # 能力具备时不动提示


def test_json_schema_false_downgrades_to_json_object(bodies):
    _adapter().complete("deepseek-flash", MSGS, SCHEMA, 16, json_schema=False)

    body = bodies[0]
    assert body["response_format"] == {"type": "json_object"}
    assert body["messages"][:len(MSGS)] == MSGS              # 原提示保持原样
    hint = body["messages"][-1]
    assert hint["role"] == "system" and "json" in hint["content"].lower()


def test_json_schema_false_keeps_prompt_when_json_word_present(bodies):
    msgs = [{"role": "user", "content": "按 JSON 结构输出"}]
    _adapter().complete("deepseek-flash", msgs, SCHEMA, 16, json_schema=False)
    assert bodies[0]["messages"] == msgs


def test_no_schema_sends_no_response_format(bodies):
    _adapter().complete("deepseek-flash", MSGS, None, 16, json_schema=False)
    assert "response_format" not in bodies[0]


class CapturingAdapter:
    """记录 LLMClient 传下来的 json_schema 能力标志。"""

    def __init__(self):
        self.schemas: list[bool | None] = []

    def complete(self, model, messages, schema, max_tokens, **kwargs):
        self.schemas.append(kwargs.get("json_schema"))
        return {"output": {"a": 1}, "usage": {"in": 1, "out": 1},
                "request_id": "req_stub", "finish_reason": "stop"}


@pytest.fixture()
def client(workspace):
    app = create_app(workspace.root)
    token = app.state.app_state.token_value
    with TestClient(app, base_url="http://127.0.0.1:8787") as c:
        c.headers.update({"X-Local-Token": token})
        c.app = app
        yield c


def test_client_passes_model_json_schema_capability(client):
    """模型级能力覆盖供应商级：json_schema=false 的模型必须收到 False。"""
    assert client.put("/api/v1/models/deepseek", json={
        "id": "deepseek", "protocol": "openai_compat", "base_url": "https://api.deepseek.com",
        "api_key": "sk-test-1234", "api_key_env": None,
        "capabilities": {"json_schema": True, "tools": True, "vision": False},
        "models": [{"id": "deepseek-flash",
                    "capabilities": {"json_schema": False, "tools": True, "vision": True}}],
    }).status_code == 200
    assert client.put("/api/v1/model-routes",
                      json={"default": "deepseek/deepseek-flash"}).status_code == 200

    cap = CapturingAdapter()
    client.app.state.app_state.client.overrides["deepseek"] = cap
    client.app.state.app_state.client.complete("default", MSGS, schema=SCHEMA, max_tokens=16)

    assert cap.schemas == [False]
