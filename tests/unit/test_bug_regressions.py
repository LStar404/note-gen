"""Bug 回归测试：行缓存污染 / agent 计划快照 / 解锁链路 / fill 预算裁剪。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from src.api.main import create_app
from src.core.paths import Layout
from src.core.store import DocStore
from src.generate.fill import fill_one

ROOT = Path(__file__).resolve().parents[2]


def test_row_cache_does_not_leak_nested_mutation(store: DocStore):
    """缓存返回的行被就地修改（含 sources 嵌套）时，不得污染后续读取。"""
    anchor = store.anchor_rows("ch01")[1]
    store.create_block(
        {"type": "definition", "title": "定义", "content": "内容",
         "sources": [{"anchor_id": anchor["anchor_id"], "quote": anchor["content"][:10],
                      "relation": "defines"}], "order": 10.0},
        author="ai", chapter_id="ch01")

    rows = store.block_rows("ch01", include_deleted=True)
    rows[0]["sources"].append({"anchor_id": "INJECTED", "quote": "x", "relation": "defines"})
    rows[0]["title"] = "被改掉的标题"

    fresh = DocStore(store.layout, store.doc_id).block_rows("ch01", include_deleted=True)
    assert not any(any(s.get("anchor_id") == "INJECTED" for s in b.get("sources", []))
                   for b in fresh), "缓存被嵌套修改污染"
    assert all(b["title"] != "被改掉的标题" for b in fresh)


def _client(sample_doc):
    layout, doc_id = sample_doc
    app = create_app(layout.root)
    token = app.state.app_state.token_value
    c = TestClient(app, base_url="http://127.0.0.1:8787")
    c.headers.update({"X-Local-Token": token})
    return c, app, doc_id


def _make_block(c, doc_id, title="待编辑"):
    anchor = DocStore(Layout(Path(c.app.state.app_state.layout.root)), doc_id).anchor_rows("ch01")[1]
    r = c.post(f"/api/v1/docs/{doc_id}/chapters/ch01/blocks", json={
        "type": "definition", "title": title, "content": "原始内容",
        "sources": [{"anchor_id": anchor["anchor_id"], "quote": anchor["content"][:10],
                     "relation": "defines"}], "order": 11.0})
    return r.json()


def test_agent_exec_aborts_when_plan_is_stale(sample_doc):
    """计划快照校验：确认后目标块被改过 → 中止剩余工具（PLAN_STALE）。"""
    c, app, doc_id = _client(sample_doc)
    block = _make_block(c, doc_id)
    st = app.state.app_state
    plan_id = "p_stale0001"
    st.runtime.save_session(
        plan_id, doc_id, f"block:{block['id']}",
        [{"role": "user", "content": "改写"}], "plan",
        confirmed_calls=[{"tool": "update_block",
                          "args": {"block_id": block["id"], "content": "agent 改写",
                                   "message": "m"}}],
        affected_revisions={block["id"]: block["revision"]})

    # 计划确认后，人工又改了一次 → 快照过期
    c.patch(f"/api/v1/blocks/{block['id']}",
            json={"content": "人工插入的修改", "expected_revision": block["revision"], "message": "human"})

    r = c.post(f"/api/v1/blocks/{block['id']}/agent-edit",
               json={"mode": "exec", "plan_id": plan_id, "confirmed": True})
    body = r.json()
    assert body.get("partial") is True
    assert body.get("error", {}).get("code") == "PLAN_STALE"
    after = c.get(f"/api/v1/blocks/{block['id']}").json()["block"]
    assert after["content"] == "人工插入的修改", "agent 不得覆盖计划确认后的并发改动"


def test_agent_unlock_allows_editing_locked_block(sample_doc):
    """解锁链路：unlock 后 agent 才能改 manually_edited 块（此前解锁参数根本没接通）。"""
    c, app, doc_id = _client(sample_doc)
    block = _make_block(c, doc_id, title="人工块")
    c.patch(f"/api/v1/blocks/{block['id']}",
            json={"content": "人工编辑过", "expected_revision": block["revision"], "message": "human"})
    st = app.state.app_state
    plan_id = "p_unlock0001"
    st.runtime.save_session(
        plan_id, doc_id, f"block:{block['id']}",
        [{"role": "user", "content": "改写"}], "plan",
        confirmed_calls=[{"tool": "update_block",
                          "args": {"block_id": block["id"], "content": "解锁后改写", "message": "m"}}],
        affected_revisions={block["id"]: 2})

    # 未解锁：应拒绝
    r = c.post(f"/api/v1/blocks/{block['id']}/agent-edit",
               json={"mode": "exec", "plan_id": plan_id, "confirmed": True})
    assert r.json().get("error", {}).get("code") == "BLOCK_LOCKED"

    # 解锁后：成功
    assert c.post(f"/api/v1/blocks/{block['id']}/unlock", json={"plan_id": plan_id}).status_code == 200
    r = c.post(f"/api/v1/blocks/{block['id']}/agent-edit",
               json={"mode": "exec", "plan_id": plan_id, "confirmed": True})
    body = r.json()
    assert body.get("partial") is not True, body
    after = c.get(f"/api/v1/blocks/{block['id']}").json()["block"]
    assert after["content"] == "解锁后改写"


def test_fill_truncates_context_when_over_budget(store: DocStore, sample_doc, tmp_path):
    """fill 超出输入预算时必须真正裁剪来源原文（原来是一行 no-op）。"""
    layout, doc_id = sample_doc
    anchors = store.anchor_rows("ch01")
    aid = anchors[1]["anchor_id"]
    long_text = "很长的原文。" * 2000
    anchors_by_id = {aid: {"anchor_id": aid, "content": long_text, "page": 1, "reading_order": 1}}
    spec = {"temp_id": "t1", "type": "definition", "title": "定义", "basis": "anchored",
            "sources": [{"anchor_id": aid, "quote": "很长", "relation": "defines"}],
            "depends_on": []}

    seen = {}

    class Spy:
        def complete(self, *a, **kw):
            seen["messages"] = kw.get("messages") or (a[1] if len(a) > 1 else [])
            return {"output": {"content": "内容", "content_format": "md_latex", "tags": [],
                               "sources_patch": [{"anchor_id": aid, "quote": "很长", "relation": "defines"}],
                               "basis": "anchored"},
                    "raw_text": "{}", "tool_calls": [], "finish_reason": "stop",
                    "usage": {"in": 1, "out": 1, "estimated": True}, "request_id": "r"}

    cfg = {"generate": {"fill_max_in": 500, "temperature": 0.2}, "output_language": "zh"}
    fill_one(Spy(), doc_id=doc_id, chapter_id="ch01", spec=spec,
             anchors_by_id=anchors_by_id, anchors=[anchors_by_id[aid]],
             model=None, config=cfg, related_titles=[])
    prompt = seen["messages"][1]["content"]
    assert len(prompt) < len(long_text), "超预算时应裁剪来源原文"
    assert f"[{aid}]" in prompt, "裁剪后仍须保留锚点 id"
