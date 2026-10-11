"""换密钥立即生效回归：适配器把 key 固化在实例里，改完必须重建客户端。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.api.main import create_app

PROVIDER = {
    "id": "dummy", "label": "Dummy", "protocol": "openai_compat",
    "base_url": "http://127.0.0.1:9", "api_key": "sk-NEW-KEY-123456",
    "capabilities": {"json_schema": True, "tools": True, "vision": False},
    "models": [{"id": "fake", "context_window": 8192, "max_output_tokens": 2048,
                "capabilities": {"json_schema": True, "tools": True, "vision": False},
                "pricing": {"input_per_million": 0, "output_per_million": 0,
                            "currency": "USD"}}],
}


def test_provider_key_change_rebuilds_client(sample_doc):
    layout, _ = sample_doc
    app = create_app(layout.root)
    c = TestClient(app, base_url="http://127.0.0.1:8787")
    c.headers.update({"X-Local-Token": app.state.app_state.token_value})
    st = app.state.app_state
    with c:
        before = st.client
        r = c.put("/api/v1/models/dummy", json=PROVIDER)
        assert r.status_code == 200, r.text
        assert st.client is not before, "改供应商后必须重建客户端，否则旧 key 一直被缓存"
        _, _, adapter = st.client._provider_for("dummy/fake")
        assert adapter.api_key == "sk-NEW-KEY-123456", "新 key 必须立刻生效"
        assert adapter.base_url == "http://127.0.0.1:9"
