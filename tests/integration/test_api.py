"""API 集成测试（E-03/E-04/E-25）：CRUD、乐观锁、选区匹配、安全边界。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.api.main import create_app


@pytest.fixture()
def client(sample_doc):
    layout, doc_id = sample_doc
    app = create_app(layout.root)
    token = app.state.app_state.token_value
    with TestClient(app, base_url="http://127.0.0.1:8787") as c:
        c.headers.update({"X-Local-Token": token})
        c.doc_id = doc_id
        c.app = app
        yield c


def test_security_requires_token(sample_doc):
    layout, doc_id = sample_doc
    app = create_app(layout.root)
    with TestClient(app, base_url="http://127.0.0.1:8787") as c:
        assert c.get("/api/v1/docs").status_code == 403
        assert c.get("/api/v1/docs", headers={"Host": "evil.example.com"}).status_code == 403
        assert c.get("/api/v1/docs", headers={"Origin": "https://evil.example.com"}).status_code == 403


def test_docs_and_blocks_flow(client):
    r = client.get("/api/v1/docs")
    assert r.status_code == 200
    assert r.json()["docs"][0]["doc_id"] == client.doc_id

    r = client.get(f"/api/v1/docs/{client.doc_id}")
    assert r.status_code == 200 and r.json()["profile"] == "textbook"

    r = client.get(f"/api/v1/docs/{client.doc_id}/chapters/ch01/blocks")
    assert r.status_code == 200
    body = r.json()
    assert body["items"] == [] and body["data_revision"] >= 1

    anchor = client.app.state.app_state.layout  # noqa: F841
    store = __import__("src.core.store", fromlist=["DocStore"]).DocStore(
        client.app.state.app_state.layout, client.doc_id)
    anchor_id = store.anchor_rows("ch01")[1]["anchor_id"]
    quote = store.anchors_by_id()[anchor_id]["content"][:12]

    r = client.post(f"/api/v1/docs/{client.doc_id}/chapters/ch01/blocks", json={
        "type": "definition", "title": "定义", "content": "内容",
        "sources": [{"anchor_id": anchor_id, "quote": quote, "relation": "defines"}],
        "order": 10.0, "message": "新建"})
    assert r.status_code == 200, r.text
    block = r.json()
    assert block["revision"] == 1 and block["generation_protected"] is True

    # 乐观锁：错误 revision → 409
    r = client.patch(f"/api/v1/blocks/{block['id']}",
                     json={"content": "改", "expected_revision": 99, "message": "编辑"})
    assert r.status_code == 409
    assert r.json()["detail"]["current_revision"] == 1

    r = client.patch(f"/api/v1/blocks/{block['id']}",
                     json={"content": "改过的内容", "expected_revision": 1, "message": "编辑"})
    assert r.status_code == 200 and r.json()["revision"] == 2
    assert r.json()["status"] == "manually_edited"

    r = client.get(f"/api/v1/blocks/{block['id']}")
    assert r.status_code == 200
    assert len(r.json()["history"]) == 2

    # 回滚到 r1
    r = client.post(f"/api/v1/blocks/{block['id']}/rollback",
                    json={"rev_id": f"{block['id']}@r1", "expected_revision": 2})
    assert r.status_code == 200
    assert r.json()["content"] == "内容"
    assert r.json()["generation_protected"] is True

    # 软删除 → 列表过滤
    r = client.request("DELETE", f"/api/v1/blocks/{block['id']}", json={"expected_revision": 3})
    assert r.status_code == 200
    r = client.get(f"/api/v1/docs/{client.doc_id}/chapters/ch01/blocks")
    assert r.json()["items"] == []
    r = client.get(f"/api/v1/docs/{client.doc_id}/chapters/ch01/blocks?include_deleted=1")
    assert len(r.json()["items"]) == 1

    # 恢复 → 反向索引重建
    r = client.post(f"/api/v1/blocks/{block['id']}/restore", json={"expected_revision": 4})
    assert r.status_code == 200
    anchor_rows = store.anchors_by_id()
    assert block["id"] in anchor_rows[anchor_id]["note_block_ids"]


def test_reorder_and_page_blocks(client):
    ids = []
    for i in range(2):
        r = client.post(f"/api/v1/docs/{client.doc_id}/chapters/ch01/blocks", json={
            "type": "motivation", "title": f"动机{i}", "content": "x", "sources": [],
            "basis": "synthesis", "order": 10.0 * (i + 1), "message": "新建"})
        ids.append(r.json()["id"])
    r = client.post(f"/api/v1/docs/{client.doc_id}/chapters/ch01/reorder",
                    json={"block_id": ids[0], "after": ids[1], "expected_revision": 1})
    assert r.status_code == 200
    assert r.json()["items"][1]["id"] == ids[0]

    r = client.get(f"/api/v1/docs/{client.doc_id}/pages/1/blocks")
    assert r.status_code == 200


def test_match_and_search(client):
    store = __import__("src.core.store", fromlist=["DocStore"]).DocStore(
        client.app.state.app_state.layout, client.doc_id)
    anchor = store.anchor_rows("ch01")[1]
    r = client.post(f"/api/v1/docs/{client.doc_id}/match", json={
        "page": 1, "rects": [anchor["bbox"]], "text": anchor["content"][:20]})
    assert r.status_code == 200
    assert r.json()["hits"][0]["anchor_id"] == anchor["anchor_id"]

    r = client.get(f"/api/v1/search?doc_id={client.doc_id}&q=determinant&mode=fts")
    assert r.status_code == 200


def test_models_and_usage(client):
    r = client.get("/api/v1/models")
    assert r.status_code == 200
    assert "providers" in r.json()

    r = client.put("/api/v1/models/ollama", json={
        "id": "ollama", "label": "本地 Ollama", "protocol": "openai_compat",
        "base_url": "http://127.0.0.1:11434/v1", "api_key": None,
        "capabilities": {"json_schema": False, "tools": True, "vision": False},
        "models": [{"id": "qwen2.5:32b", "context_window": 8192, "max_output_tokens": 2048,
                    "capabilities": {"json_schema": False, "tools": True, "vision": False},
                    "pricing": {"input_per_million": 0, "output_per_million": 0, "currency": "USD"}}]})
    assert r.status_code == 200

    r = client.put("/api/v1/model-routes", json={"fill": "ollama/qwen2.5:32b"})
    assert r.status_code == 200

    r = client.put("/api/v1/model-routes", json={"agent": "ollama/qwen2.5:32b"})
    assert r.status_code == 200

    r = client.get("/api/v1/usage")
    assert r.status_code == 200


def test_export_task(client):
    r = client.post("/api/v1/export", json={"doc_id": client.doc_id, "chapter_id": "ch01",
                                            "format": "md"})
    assert r.status_code == 202
    task_id = r.json()["task_id"]
    import time

    for _ in range(50):
        task = client.get(f"/api/v1/tasks/{task_id}").json()
        if task["status"] in ("succeeded", "failed", "partial"):
            break
        time.sleep(0.1)
    assert task["status"] == "succeeded", task
