"""单块 AI 编辑与知识点关联回归：建议不落盘、应用=人工确认、关联校验。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.api.main import create_app
from src.core.store import DocStore


class EditClient:
    def route(self, task):
        return "fake/fake"

    def complete(self, task, messages, **kw):
        # 改写是纯文本任务：json_schema 强制 JSON 与「不要输出 JSON」指令打架（实测回空格）
        assert kw.get("schema") is None, "AI 改写不得用 JSON schema"
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


def test_ai_edit_handles_unstable_model_output_shapes(sample_doc):
    """真实模型输出形状不稳（实测）：{text:...}/{md_latex:...} 套壳/纯文本/空白全接住。
    json_schema 禁 JSON 与指令禁止 JSON 打架会让模型回空格——现已纯文本调用+解壳。"""
    from src.api.routes.docs import _unwrap_proposal

    assert _unwrap_proposal('{"md_latex": "正文内容"}') == "正文内容"
    assert _unwrap_proposal("```\n正文\n```") == "正文"
    assert _unwrap_proposal("普通正文") == "普通正文"

    layout, doc_id = sample_doc
    store = DocStore(layout, doc_id)
    ch = store.chapter_ids()[0]
    block = _seed(store, ch)

    class TextShapeClient:
        def route(self, task):
            return "fake/fake"

        def complete(self, task, messages, **kw):
            assert kw.get("schema") is None, "改写不得用 JSON schema（与纯文本指令打架）"
            return {"output": {"text": "改好的正文。"}, "usage": {}}

    app = create_app(layout.root)
    with TestClient(app, base_url="http://127.0.0.1:8787") as client:
        headers = {"X-Local-Token": app.state.app_state.token_value}
        app.state.app_state.client = TextShapeClient()
        r = client.post(f"/api/v1/docs/{doc_id}/blocks/{block['id']}/ai-edit",
                        json={"instruction": "更精简"}, headers=headers)
        assert r.status_code == 200, r.text
        assert r.json()["proposal"] == "改好的正文。", r.json()

        class SpaceClient(TextShapeClient):
            def complete(self, task, messages, **kw):
                return {"output": " " * 40, "usage": {}}

        app.state.app_state.client = SpaceClient()
        r = client.post(f"/api/v1/docs/{doc_id}/blocks/{block['id']}/ai-edit",
                        json={"instruction": "更精简"}, headers=headers)
        assert r.status_code == 400, "空白产出必须拒收: " + r.text


def test_deleted_blocks_invisible_to_agent_and_panes(sample_doc):
    """软删块不得被 agent 编辑/关联、不得出现在教材侧栏（include_deleted 默认含已删块的坑）。"""
    import pytest

    from src.api.agent_edit import run_tool
    from src.core.errors import NotFound

    layout, doc_id = sample_doc
    store = DocStore(layout, doc_id)
    ch = store.chapter_ids()[0]
    a = _seed(store, ch)
    b = store.create_block({"type": "motivation", "title": "块B", "content": "内容 B。",
                            "sources": [], "basis": "synthesis", "order": 2.0},
                           author="human", chapter_id=ch)
    store.soft_delete_block(a["id"], expected_revision=a["revision"])
    with pytest.raises(NotFound):
        run_tool(store, "update_block",
                 {"block_id": a["id"], "content": "x", "message": "m"},
                 author="agent", unlocked_for_agent=True)
    with pytest.raises(NotFound):
        run_tool(store, "link_blocks",
                 {"from_block_id": b["id"], "to_block_id": a["id"]},
                 author="agent", unlocked_for_agent=True)

    app = create_app(layout.root)
    with TestClient(app, base_url="http://127.0.0.1:8787") as client:
        headers = {"X-Local-Token": app.state.app_state.token_value}
        for page in range(1, 4):
            r = client.get(f"/api/v1/docs/{doc_id}/pages/{page}/blocks", headers=headers)
            assert r.status_code == 200, r.text
            ids = [x["block_id"] for x in r.json()]
            assert a["id"] not in ids, f"教材侧栏不得展示已删块: page {page} -> {ids}"


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
