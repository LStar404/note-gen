"""重建导入回归：免重新上传拾取新解析/分章规则；有笔记块必须拒绝。"""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from src.api.main import create_app


def _wait(c, tid, budget=120.0):
    t = {"status": "n/a"}
    deadline = time.time() + budget
    while time.time() < deadline:
        t = c.get(f"/api/v1/tasks/{tid}").json()
        if t["status"] in ("succeeded", "failed", "partial", "cancelled", "interrupted"):
            return t
        time.sleep(0.25)
    return t


def test_rebuild_redetects_chapters(sample_doc):
    layout, doc_id = sample_doc
    app = create_app(layout.root)
    token = app.state.app_state.token_value
    with TestClient(app, base_url="http://127.0.0.1:8787") as c:
        c.headers.update({"X-Local-Token": token})
        r = c.post(f"/api/v1/docs/{doc_id}/rebuild", json={})
        assert r.status_code == 202, r.text
        t = _wait(c, r.json()["task_id"])
        assert t["status"] == "succeeded", t
        chapters = t["result"]["chapters"]
        assert chapters, "重建后必须有章节"
        m = c.get(f"/api/v1/docs/{doc_id}").json()
        assert [x["chapter_id"] for x in m["chapters"]] == [x["chapter_id"] for x in chapters]
        # 锚点已按新章节重建
        ch = chapters[0]["chapter_id"]
        assert c.get(f"/api/v1/docs/{doc_id}/chapters/{ch}/blocks").status_code == 200


def test_rebuild_rejected_when_blocks_exist(sample_doc):
    layout, doc_id = sample_doc
    app = create_app(layout.root)
    token = app.state.app_state.token_value
    with TestClient(app, base_url="http://127.0.0.1:8787") as c:
        c.headers.update({"X-Local-Token": token})
        ch = c.get(f"/api/v1/docs/{doc_id}").json()["chapters"][0]["chapter_id"]
        r = c.post(f"/api/v1/docs/{doc_id}/chapters/{ch}/blocks",
                   json={"type": "motivation", "title": "t", "content": "x",
                         "sources": [], "basis": "synthesis", "order": 1.0})
        assert r.status_code in (200, 201), r.text
        r2 = c.post(f"/api/v1/docs/{doc_id}/rebuild", json={})
        assert r2.status_code == 409, r2.text
        assert "笔记块" in r2.text
