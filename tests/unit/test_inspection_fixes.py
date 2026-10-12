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