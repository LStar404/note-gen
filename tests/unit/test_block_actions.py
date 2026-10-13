"""单块 AI 编辑与知识点关联回归：建议不落盘、应用=人工确认、关联校验。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.api.main import create_app
from src.core.store import DocStore


class EditClient:
    def route(self, task):
        return "fake/fake"

    def complete(self, task, messages, **kw):
        assert kw.get("schema"), "AI 编辑必须走结构化输出"
        return {"output": {"content": "修改后的笔记正文，保留 [1] 引文标记。"},
                "usage": {}}


def _seed(store, ch):
    return store.create_block({"type": "motivation", "title": "块A",
                               "content": "原始内容 A。", "sources": [],
                               "basis": "synthesis", "order": 1.0},
                              author="human", chapter_id=ch)


def test_ai_edit_propose_does_not_mutate_then_apply_records_history(sample_doc):
    layout, doc_id = sample_doc
    store = DocStore(layout, doc_id)
    ch = store.chapter_ids()[0]
    block = _seed(store, ch)
    app = create_app(layout.root)
    with TestClient(app, base_url="http://127.0.0.1:8787") as client:
        headers = {"X-Local-Token": app.state.app_state.token_value}
        app.state.app_state.client = EditClient()
        r = client.post(f"/api/v1/docs/{doc_id}/blocks/{block['id']}/ai-edit",
                        json={"instruction": "更精简"}, headers=headers)
        assert r.status_code == 200, r.text
        assert r.json()["proposal"]
        cur = DocStore(layout, doc_id).blocks_by_id()[block["id"]]
        assert cur["content"] == "原始内容 A。", "建议阶段不得改写块"
        r = client.post(f"/api/v1/docs/{doc_id}/blocks/{block['id']}/apply-edit",
                        json={"content": r.json()["proposal"],
                              "base_revision": cur["revision"]}, headers=headers)
        assert r.status_code == 200, r.text
        upd = r.json()["block"]
        assert "修改后" in upd["content"]
        assert upd["generation_protected"] and upd["status"] == "manually_edited"
        rows = DocStore(layout, doc_id).history_rows(block["id"])
        snaps = [(h.get("snapshot") or {}).get("content") or "" for h in rows]
        assert any("修改后" in s for s in snaps), f"历史必须记录新内容: {snaps}"
        assert any("原始内容 A" in s for s in snaps), f"历史必须可回溯原内容: {snaps}"


def test_links_add_remove_and_validation(sample_doc):
    layout, doc_id = sample_doc
    store = DocStore(layout, doc_id)
    ch = store.chapter_ids()[0]
    a = _seed(store, ch)
    b = DocStore(layout, doc_id).create_block({"type": "motivation", "title": "块B",
                                               "content": "原始内容 B。", "sources": [],
                                               "basis": "synthesis", "order": 2.0},
                                              author="human", chapter_id=ch)
    app = create_app(layout.root)
    with TestClient(app, base_url="http://127.0.0.1:8787") as client:
        headers = {"X-Local-Token": app.state.app_state.token_value}
        r = client.post(f"/api/v1/docs/{doc_id}/blocks/{a['id']}/links",
                        json={"target_id": a["id"]}, headers=headers)
        assert r.status_code >= 400, "不能关联自己"
        r = client.post(f"/api/v1/docs/{doc_id}/blocks/{a['id']}/links",
                        json={"target_id": "b_nope"}, headers=headers)
        assert r.status_code == 404, r.text
        r = client.post(f"/api/v1/docs/{doc_id}/blocks/{a['id']}/links",
                        json={"target_id": b["id"]}, headers=headers)
        assert r.status_code == 200, r.text
        assert b["id"] in r.json()["block"]["links"]
        r = client.delete(
            f"/api/v1/docs/{doc_id}/blocks/{a['id']}/links/{b['id']}", headers=headers)
        assert r.status_code == 200, r.text
        assert r.json()["block"]["links"] == []
