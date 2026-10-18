"""槽位配置回归：向导暴露的槽位必须与后端真实消费的一致（ocr/quiz 曾配了被静默丢弃）。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.api.main import create_app
from src.api.setup_guide import SLOT_LABELS

CONSUMED_BY_BACKEND = {"default", "outline", "fill", "agent", "link", "quiz", "ocr"}


def test_slot_labels_cover_all_backend_slots():
    assert set(SLOT_LABELS) == CONSUMED_BY_BACKEND, \
        "向导暴露的槽位与后端消费的槽位必须一致（否则配置会静默丢失）"


def _client_with_model(layout, *, vision: bool):
    app = create_app(layout.root)
    state = app.state.app_state
    data = state.registry.load()
    prov = dict(data["providers"][0])
    mid = "vision-model" if vision else "blind-model"
    prov["capabilities"] = {"json_schema": True, "tools": True, "vision": vision}
    prov["models"] = [{"id": mid, "context_window": 8000, "max_output_tokens": 2000,
                       "capabilities": {"json_schema": True, "tools": True, "vision": vision}}]
    data["providers"] = [prov]
    state.registry.save(data)
    state.invalidate_client()
    return app, f"{prov['id']}/{mid}"


def test_update_routes_persists_ocr_and_quiz(workspace):
    app, model = _client_with_model(workspace, vision=True)
    with TestClient(app, base_url="http://127.0.0.1:8787") as client:
        headers = {"X-Local-Token": app.state.app_state.token_value}
        r = client.put("/api/v1/model-routes", json={"ocr": model, "quiz": model},
                       headers=headers)
        assert r.status_code == 200, r.text
        routes = r.json()["routes"]
        assert routes.get("ocr") == model, "ocr 槽位被静默丢弃"
        assert routes.get("quiz") == model, "quiz 槽位被静默丢弃"


def test_update_routes_rejects_non_vision_ocr(workspace):
    app, model = _client_with_model(workspace, vision=False)
    with TestClient(app, base_url="http://127.0.0.1:8787") as client:
        headers = {"X-Local-Token": app.state.app_state.token_value}
        r = client.put("/api/v1/model-routes", json={"ocr": model}, headers=headers)
        assert r.status_code == 400, r.text
        assert "vision" in r.text
