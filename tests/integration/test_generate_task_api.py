"""生成任务 API 回归：重复点击不得重复排任务（B6）。"""

from __future__ import annotations

import threading

from fastapi.testclient import TestClient

from src.api.main import create_app


def test_generate_submit_dedupes_running_task(sample_doc, monkeypatch):
    layout, doc_id = sample_doc
    app = create_app(layout.root)
    token = app.state.app_state.token_value
    started = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    def fake_generate(doc_id, chapter_id, **kw):
        calls.append(chapter_id)
        started.set()
        assert release.wait(10)
        return {"chapter_id": chapter_id, "status": "complete", "created": [],
                "updated": [], "skipped_protected": [], "failures": []}

    monkeypatch.setattr("src.generate.engine.generate_chapter", fake_generate)
    with TestClient(app, base_url="http://127.0.0.1:8787") as c:
        c.headers.update({"X-Local-Token": token})
        r1 = c.post(f"/api/v1/docs/{doc_id}/generate", json={"chapter_id": "ch01"})
        assert r1.status_code == 202, r1.text
        tid1 = r1.json()["task_id"]
        assert started.wait(10)

        r2 = c.post(f"/api/v1/docs/{doc_id}/generate", json={"chapter_id": "ch01"})
        assert r2.status_code == 202, r2.text
        assert r2.json()["task_id"] == tid1, "运行中重复提交应复用同一任务"

        release.set()
        th = None
        with c.app.state.app_state.tasks._lock:
            th = c.app.state.app_state.tasks._threads.get(tid1)
        if th is not None:
            th.join(10)

    assert calls == ["ch01"], f"generate_chapter 只应被调用一次: {calls}"
