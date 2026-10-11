"""删除教材反馈数据回归：DELETE 必须返回移除清单与回收站位置（UI 删除反馈依赖它）。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.api.main import create_app


def test_delete_doc_returns_removal_summary(sample_doc):
    layout, doc_id = sample_doc
    app = create_app(layout.root)
    token = app.state.app_state.token_value
    with TestClient(app, base_url="http://127.0.0.1:8787") as c:
        c.headers.update({"X-Local-Token": token})
        # 先放一个块，让 removed 统计非零
        ch = c.get(f"/api/v1/docs/{doc_id}").json()["chapters"][0]["chapter_id"]
        r = c.post(f"/api/v1/docs/{doc_id}/chapters/{ch}/blocks",
                   json={"type": "motivation", "title": "t", "content": "x",
                         "sources": [], "basis": "synthesis", "order": 1.0})
        assert r.status_code in (200, 201), r.text

        r = c.request("DELETE", f"/api/v1/docs/{doc_id}", json={"confirmed": True})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["doc_id"] == doc_id
        assert body["recoverable"] is True
        assert body["trashed_to"], "必须告知回收站位置（删除反馈）"
        removed = body["removed"]
        assert removed["blocks"] >= 1, removed
        assert removed["anchors"] >= 1, removed
        assert removed["chapters"] >= 1, removed

        # 未确认必须拒绝（防误删）
        r2 = c.request("DELETE", f"/api/v1/docs/{doc_id}", json={})
        assert r2.status_code == 400, r2.text
