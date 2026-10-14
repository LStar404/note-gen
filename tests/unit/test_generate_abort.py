"""生成中止回归：余额耗尽/密钥失效是全局故障，必须中止余下章节并报清原因。"""

from __future__ import annotations

import json
import time

from fastapi.testclient import TestClient

from src.api.main import create_app
from src.core.errors import QuotaExhausted
from src.core.store import DocStore


def test_quota_exhaustion_aborts_remaining_chapters(sample_doc, monkeypatch):
    """曾逐章重试到全部失败：用户只看到「部分完成」，不知道是没余额。"""
    layout, doc_id = sample_doc
    chapters = DocStore(layout, doc_id).chapter_ids()
    assert len(chapters) >= 2, "样本书至少两章"
    calls: list[str] = []

    def fake_gen(doc_id_, ch, **kw):
        calls.append(ch)
        raise QuotaExhausted("模型 deepseek-flash 调用失败：HTTP 402 Insufficient Balance")

    monkeypatch.setattr("src.generate.engine.generate_chapter", fake_gen)
    app = create_app(layout.root)
    with TestClient(app, base_url="http://127.0.0.1:8787") as client:
        headers = {"X-Local-Token": app.state.app_state.token_value}
        client.put("/api/v1/settings", json={"generate": {"chapter_concurrency": 1}},
                   headers=headers)
        r = client.post(f"/api/v1/docs/{doc_id}/generate", json={}, headers=headers)
        assert r.status_code == 202, r.text
        task_id = r.json()["task_id"]
        for _ in range(200):
            t = client.get(f"/api/v1/tasks/{task_id}", headers=headers).json()
            if t["status"] not in ("queued", "running"):
                break
            time.sleep(0.05)
        assert t["status"] == "failed", t
        msg = json.dumps(t.get("error") or {}, ensure_ascii=False)
        assert "生成中止" in msg and "402" in msg, msg
        assert "继续生成" in msg, "中止消息必须告诉用户怎么续跑"
    assert len(calls) < len(chapters), \
        f"配额故障后余下章节不得再调用 LLM（已调 {calls} / 共 {len(chapters)} 章）"
