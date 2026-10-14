"""引文修复回归：同章多块必须一次提交（校验是整章范围）、修复只动引文、可选解锁。"""

from __future__ import annotations

import json

from scripts.integrity_report import fix_quotes, scan_doc
from src.core.jsonl import atomic_write_bytes, dump_line
from src.core.store import DocStore
from src.core.text import normalize_text


def _plant_bad_quote(store: DocStore, layout, doc_id: str, chapter_id: str, anchor: dict) -> str:
    """直接写一条「引文与锚点不符」的块（模拟历史遗留数据，绕过创建期校验）。"""
    # block_id 必须 12 位 base62（check_block_id 校验）
    block_id = "b_" + ("t" + anchor["anchor_id"][-6:] + "00000")[:12]
    row = {
        "id": block_id, "doc_id": doc_id, "chapter_id": chapter_id, "type": "theorem",
        "title": "测试块", "content": "正文内容保持不变。", "content_format": "md_latex",
        "basis": "anchored", "status": "manually_edited", "created_by": "ai",
        "generation_protected": True, "revision": 1, "order": 900.0,
        "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
        "sources": [{"anchor_id": anchor["anchor_id"], "quote": "这段引文根本不在锚点原文里",
                     "relation": "states"}],
        "links": [], "tags": [], "deleted_at": None,
    }
    path = layout.blocks(doc_id, chapter_id)
    rows = ([json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
            if path.exists() else [])
    rows.append(row)
    atomic_write_bytes(path, ("".join(dump_line(r) + "\n" for r in rows)).encode("utf-8"))
    # 同步反向索引，否则提交时的 I3 双向镜像校验失败
    apath = layout.anchors(doc_id, chapter_id)
    arows = [json.loads(x) for x in apath.read_text(encoding="utf-8").splitlines() if x.strip()]
    for a in arows:
        if a["anchor_id"] == anchor["anchor_id"]:
            a.setdefault("note_block_ids", [])
            a["note_block_ids"] = list(a["note_block_ids"]) + [block_id]
    atomic_write_bytes(apath, ("".join(dump_line(a) + "\n" for a in arows)).encode("utf-8"))
    store._invalidate(layout, doc_id)
    return block_id


def test_fix_quotes_batches_whole_chapter_and_unlocks(sample_doc):
    layout, doc_id = sample_doc
    store = DocStore(layout, doc_id)
    chapter_id = store.chapter_ids()[0]
    anchors = [a for a in store.anchor_rows(chapter_id) if (a.get("content") or "").strip()][:2]
    assert len(anchors) == 2, "夹具需要至少两个锚点"
    ids = [_plant_bad_quote(store, layout, doc_id, chapter_id, a) for a in anchors]

    st = scan_doc(DocStore(layout, doc_id))
    bad_blocks = {b for b, _a, _o, _n in st["bad_quotes"]}
    assert bad_blocks == set(ids), (bad_blocks, ids)

    fixed, skipped = fix_quotes(DocStore(layout, doc_id), st["bad_quotes"], apply=True, unlock=True)
    assert (fixed, skipped) == (2, 0), (fixed, skipped)

    after = DocStore(layout, doc_id)
    assert scan_doc(after)["bad_quotes"] == [], "整章一次性提交后不得再有坏引文"
    amap = after.anchors_by_id()
    for bid in ids:
        b = after.get_block(bid, include_deleted=False)
        assert b["content"] == "正文内容保持不变。", "修复只动引文，正文必须原样"
        assert b["generation_protected"] is False, "unlock=True 时清保护位"
        for s in b["sources"]:
            assert normalize_text(s["quote"]) in normalize_text(amap[s["anchor_id"]]["content"])
        msgs = [h.get("message") for h in after.history_rows(bid)]
        assert "修复与锚点不符的引文" in msgs, msgs
