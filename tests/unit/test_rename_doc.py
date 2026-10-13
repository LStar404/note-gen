"""教材改名回归：只改 manifest.title，走提交机制（单写者/CAS）。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.api.main import create_app
from src.core.store import DocStore


def test_rename_doc_updates_title(sample_doc):
    layout, doc_id = sample_doc
    app = create_app(layout.root)
    with TestClient(app, base_url="http://127.0.0.1:8787") as client:
        headers = {"X-Local-Token": app.state.app_state.token_value}
        r = client.post(f"/api/v1/docs/{doc_id}/rename", json={"title": "新书名"}, headers=headers)
        assert r.status_code == 200, r.text
        assert r.json()["title"] == "新书名"
        man = client.get(f"/api/v1/docs/{doc_id}", headers=headers).json()
        assert man["title"] == "新书名", man
        assert DocStore(layout, doc_id).manifest()["title"] == "新书名"
        r2 = client.post(f"/api/v1/docs/{doc_id}/rename", json={"title": "   "}, headers=headers)
        assert r2.status_code >= 400, "空名必须拒绝"
        r3 = client.post("/api/v1/docs/doc-nope/rename", json={"title": "x"}, headers=headers)
        assert r3.status_code == 404, r3.text
