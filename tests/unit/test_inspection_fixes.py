"""巡检修复回归：锚点链接误删 / vec 相似度换算 / 建块字段污染。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.api.main import create_app
from src.core.store import DocStore
from src.db.vec import cosine_from_l2
from src.link.backlink import link_doc


def test_backlink_drops_illegal_anchor_links(sample_doc):
    """4.6.2：links 只允许块 id/URL/ref_——锚点引用走 sources；
    非法目标必须清理并记 dead_links（校验器同样拒绝其进入）。"""
    import json

    layout, doc_id = sample_doc
    store = DocStore(layout, doc_id)
    aid = store.anchor_rows("ch01")[3]["anchor_id"]
    blk = store.create_block(
        {"title": "链到锚点", "content": "c", "type": "link", "order": 99.0,
         "basis": "synthesis", "sources": [], "links": []},
        author="ai", chapter_id="ch01")
    # 直写 JSONL 注入非法链接（校验器不允许建入，但旧数据/离线写入可能存在）
    p = layout.blocks(doc_id, "ch01")
    rows = [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]
    for r in rows:
        if r["id"] == blk["id"]:
            r["links"] = [aid]
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + chr(10) for r in rows),
                 encoding="utf-8")
    DocStore._invalidate(layout, doc_id)
    rep = link_doc(doc_id, chapter_id="ch01", layout=layout)
    got = store.get_block(blk["id"])
    assert aid not in got.get("links", []), got.get("links")
    assert any(d["link"] == aid for d in rep.get("dead_links", [])), rep.get("dead_links")


def test_cosine_from_l2_matches_true_cosine():
    """向量归一化后 d 与 cos 的换算：cos = 1 - d²/2（旧实现 1-d 错位阈值）。"""
    assert cosine_from_l2(0.0) == 1.0                    # 同向
    assert abs(cosine_from_l2(1.0) - 0.5) < 1e-9         # 60°：d=1, cos=0.5
    assert abs(cosine_from_l2(2.0) + 1.0) < 1e-9         # 反向


def test_create_block_does_not_store_message_field(workspace, sample_doc):
    """message 是历史备注：旧实现把它当块字段永久写进记录（extra=allow 放行）。"""
    layout, doc_id = sample_doc
    app = create_app(workspace.root)
    with TestClient(app, base_url="http://127.0.0.1:8787") as client:
        headers = {"X-Local-Token": app.state.app_state.token_value}
        resp = client.post(
            "/api/v1/docs/" + doc_id + "/chapters/ch01/blocks", headers=headers,
            json={"title": "新块", "content": "内容", "type": "link", "order": 50.0,
                  "basis": "synthesis", "message": "这是历史备注"})
        assert resp.status_code < 400, resp.text
        blk = resp.json()
        assert "message" not in blk, "message 不得进入块记录"
        store = DocStore(layout, doc_id)
        assert "message" not in store.get_block(blk["id"]), "入库记录不得含 message 字段"


def test_agent_link_blocks_respects_manual_unlock(sample_doc):
    """计划已解锁的手工块可被 link_blocks 修改（run_tool 曾漏传解锁标志）。
    同时验证未解锁时仍按 4.5.2 拒绝。"""
    from src.api.agent_edit import run_tool
    from src.core.errors import BlockLocked

    layout, doc_id = sample_doc
    store = DocStore(layout, doc_id)
    b1 = store.create_block({"title": "甲", "content": "c", "type": "link", "order": 11.0,
                             "basis": "synthesis", "sources": []},
                            author="human", chapter_id="ch01")
    b2 = store.create_block({"title": "乙", "content": "c", "type": "link", "order": 12.0,
                             "basis": "synthesis", "sources": []},
                            author="human", chapter_id="ch01")
    try:
        run_tool(store, "link_blocks",
                 {"from_block_id": b1["id"], "to_block_id": b2["id"]},
                 author="agent", unlocked_for_agent=False)
        raise AssertionError("未解锁时应拒绝")
    except BlockLocked:
        pass
    run_tool(store, "link_blocks",
             {"from_block_id": b1["id"], "to_block_id": b2["id"]},
             author="agent", unlocked_for_agent=True)
    got = store.get_block(b1["id"])
    assert b2["id"] in (got.get("links") or []), got.get("links")


def test_chapter_finish_clamps_inverted_ranges():
    """同页起章会让 page_end 倒挂（次章起始-1）：必须钳到起点，不得产出空区间章。"""
    from src.ingest.chapter_detect import _finish

    out = _finish([
        {"chapter_id": "ch01", "title": "A", "page_start": 5, "page_end": 4},
        {"chapter_id": "ch02", "title": "B", "page_start": 5, "page_end": 20},
    ], 30)
    assert out[0]["page_end"] >= out[0]["page_start"], out[0]
    assert out[0]["page_end"] == 5
    assert out[1]["page_start"] == 5 and out[1]["page_end"] == 20