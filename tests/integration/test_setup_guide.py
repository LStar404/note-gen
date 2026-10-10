"""配置指南 API 集成测试（ADR D35）：自检报告、就绪度、掩码密钥回写安全。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.api.main import create_app


@pytest.fixture()
def client(workspace):
    app = create_app(workspace.root)
    token = app.state.app_state.token_value
    with TestClient(app, base_url="http://127.0.0.1:8787") as c:
        c.headers.update({"X-Local-Token": token})
        c.app = app
        yield c


def test_setup_status_requires_token(workspace):
    app = create_app(workspace.root)
    with TestClient(app, base_url="http://127.0.0.1:8787") as c:
        assert c.get("/api/v1/setup/status").status_code == 403


def test_setup_status_shape_and_first_run(client):
    body = client.get("/api/v1/setup/status").json()

    # 六步向导顺序固定；required 标记与 spec 一致
    assert [s["id"] for s in body["steps"]] == ["env", "model", "routes", "parser", "output", "budget"]
    assert all({"id", "title", "required", "summary", "done", "detail"} <= set(s) for s in body["steps"])

    # 全新工作区：未接模型 → 未就绪、首次使用
    assert body["first_run"] is True
    assert body["ready"] is False
    by_id = {c["id"]: c for c in body["checks"]}
    assert by_id["python"]["level"] == "ok"
    assert by_id["data_dir"]["level"] == "ok"
    assert by_id["models"]["level"] == "fail"
    assert by_id["models"]["fix"]           # 必须给出可执行的修复提示
    assert body["options"]["parsers"] == ["auto", "marker", "docling", "pypdf"]
    assert body["options"]["ocr_modes"] == ["auto", "force", "off"]
    assert body["options"]["languages"] == ["zh", "en", "ja"]
    assert body["settings"]["generate"]["output_language"] == "zh"
    # steps 的 done 与 checks 一致：模型未接入 → model 步未完成
    steps = {s["id"]: s for s in body["steps"]}
    assert steps["env"]["done"] is True
    assert steps["model"]["done"] is False


def test_setup_ready_after_model_routes_parser(client):
    # 1) 接入供应商 + 模型 + 密钥
    r = client.put("/api/v1/models/openai", json={
        "id": "openai", "label": "OpenAI", "protocol": "openai_compat",
        "base_url": "https://api.openai.com/v1",
        "api_key": "sk-test-1234", "api_key_env": None,
        "capabilities": {"json_schema": True, "tools": True, "vision": False},
        "models": [{"id": "gpt-4o-mini",
                    "capabilities": {"json_schema": True, "tools": True, "vision": False},
                    "pricing": {"input_per_million": 0.15, "output_per_million": 0.6}}],
    })
    assert r.status_code == 200
    assert r.json()["api_key"].endswith("1234") and "*" in r.json()["api_key"]

    # 2) 路由 default
    r = client.put("/api/v1/model-routes", json={"default": "openai/gpt-4o-mini"})
    assert r.status_code == 200

    # 3) 解析器切到主环境可用的 pypdf
    r = client.put("/api/v1/settings", json={"parser": {"default": "pypdf", "ocr_mode": "auto"}})
    assert r.status_code == 200

    body = client.get("/api/v1/setup/status").json()
    assert body["ready"] is True
    assert body["first_run"] is False            # config.yaml 已生成
    steps = {s["id"]: s for s in body["steps"]}
    assert steps["model"]["done"] is True
    assert steps["routes"]["done"] is True
    assert steps["parser"]["done"] is True
    assert steps["output"]["done"] is True
    prov = {p["id"]: p for p in body["providers"]}["openai"]
    assert prov["key_configured"] is True and prov["key_source"] == "plain"
    assert prov["key_masked"].endswith("1234")

    # 已路由模型价格已知 + 非零预算 → price 检查 ok（5.3.5 不会触发）
    r = client.put("/api/v1/settings", json={"generate": {"max_cost_per_chapter": 0.5}})
    assert r.status_code == 200
    body = client.get("/api/v1/setup/status").json()
    assert {c["id"]: c["level"] for c in body["checks"]}["price"] == "ok"


def test_masked_key_roundtrip_preserves_real_key(client):
    client.put("/api/v1/models/openai", json={
        "id": "openai", "protocol": "openai_compat", "base_url": "https://api.openai.com/v1",
        "api_key": "sk-secret-9876", "api_key_env": None,
        "models": [{"id": "m1"}],
    })
    masked = client.get("/api/v1/models").json()["providers"][0]["api_key"]
    assert masked != "sk-secret-9876" and masked.endswith("9876")

    # 界面把掩码值原样回传（旧实现会把真 key 覆盖成掩码）
    client.put("/api/v1/models/openai", json={
        "id": "openai", "protocol": "openai_compat", "base_url": "https://api.openai.com/v1",
        "api_key": masked, "api_key_env": None,
        "models": [{"id": "m1"}],
    })
    raw = client.app.state.app_state.registry.provider("openai")
    assert raw["api_key"] == "sk-secret-9876"

    # 显式输入新 key 仍然生效
    client.put("/api/v1/models/openai", json={
        "id": "openai", "protocol": "openai_compat", "base_url": "https://api.openai.com/v1",
        "api_key": "sk-new-0001", "api_key_env": None,
        "models": [{"id": "m1"}],
    })
    assert client.app.state.app_state.registry.provider("openai")["api_key"] == "sk-new-0001"


def test_key_without_models_reports_model_gap_not_missing_key(client):
    """回归：有密钥但零模型时，keys 检查不得误报「尚未配置任何 API Key」。

    旧实现用同一个 any_keyed（密钥 + 模型）驱动 models 与 keys 两项检查，
    用户实际处境（深寻密钥已存、但 deepseek 无模型）会被提示成「没配密钥」，
    从而被引向反复轮换密钥的死路。
    """
    r = client.put("/api/v1/models/deepseek", json={
        "id": "deepseek", "label": "ds", "protocol": "openai_compat",
        "base_url": "https://api.deepseek.com", "api_key": "sk-fdc0000000000000000000000003fb71e",
        "api_key_env": None, "models": [],
    })
    assert r.status_code == 200

    body = client.get("/api/v1/setup/status").json()
    checks = {c["id"]: c for c in body["checks"]}
    assert checks["keys"]["level"] == "warn"
    assert "尚未配置任何 API Key" not in checks["keys"]["detail"]
    assert "deepseek" in checks["keys"]["detail"] and "未登记模型" in checks["keys"]["detail"]
    assert "401" in checks["keys"]["fix"]           # 明确指向「密钥可能无效」这一后续结论

    assert checks["models"]["level"] == "fail"
    assert "deepseek" in checks["models"]["fix"] and "补至少一个模型" in checks["models"]["fix"]

    prov = {p["id"]: p for p in body["providers"]}["deepseek"]
    assert prov["key_configured"] is True and prov["model_count"] == 0
    assert body["ready"] is False
    assert {s["id"]: s for s in body["steps"]}["model"]["done"] is False


def test_setup_agent_slot_requires_tools(client):
    client.put("/api/v1/models/openai", json={
        "id": "openai", "protocol": "openai_compat", "base_url": "https://api.openai.com/v1",
        "api_key": "sk-test-1234", "api_key_env": None,
        "models": [{"id": "plain", "capabilities": {"json_schema": False, "tools": False, "vision": False}},
                   {"id": "tooly", "capabilities": {"json_schema": True, "tools": True, "vision": False}}],
    })
    r = client.put("/api/v1/model-routes", json={"default": "openai/plain", "agent": "openai/plain"})
    assert r.status_code >= 400
    r = client.put("/api/v1/model-routes", json={"default": "openai/plain", "agent": "openai/tooly"})
    assert r.status_code == 200

    body = client.get("/api/v1/setup/status").json()
    levels = {c["id"]: c["level"] for c in body["checks"]}
    assert levels["agent_tools"] == "ok"
