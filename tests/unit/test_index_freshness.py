"""派生索引新鲜度回归：直改 JSONL（离线修复路径）后检索/锚点面板不得返回旧内容。"""

from __future__ import annotations

import glob
import json

from src.db import queries
from src.db.reindex import (anchors_fingerprint, blocks_fingerprint, ensure_index_fresh,
                            index_meta, reindex_doc, source_fingerprint)

NL = chr(10)


def _anchor_files(layout, doc_id):
    return sorted(glob.glob(str(layout.data_dir / doc_id / "anchors" / "*.jsonl")))


def test_source_fingerprint_tracks_content():
    a1 = [{"anchor_id": "x", "text_hash": "h1"}]
    a2 = [{"anchor_id": "x", "text_hash": "h2"}]
    b1 = [{"id": "b", "revision": 1, "title": "t"}]
    assert source_fingerprint(a1, b1) != source_fingerprint(a2, b1)
    assert source_fingerprint(a1, b1) != source_fingerprint(a1, [{"id": "b", "revision": 2, "title": "t"}])
    a3 = [{"anchor_id": "y", "text_hash": "h3"}, {"anchor_id": "x", "text_hash": "h1"}]
    a4 = [{"anchor_id": "x", "text_hash": "h1"}, {"anchor_id": "y", "text_hash": "h3"}]
    assert source_fingerprint(a3, b1) == source_fingerprint(a4, b1)


def test_ensure_index_fresh_rebuilds_after_direct_edit(sample_doc):
    layout, doc_id = sample_doc
    reindex_doc(layout, doc_id)
    assert ensure_index_fresh(layout, doc_id) is False

    files = _anchor_files(layout, doc_id)
    assert files, "示例文档应有锚点文件"
    rows = [json.loads(l) for l in open(files[0], encoding="utf-8") if l.strip()]
    rows[0]["content"] = "DIRECT-EDIT-MARKER 内容被离线修复改写"
    with open(files[0], "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + NL)

    assert ensure_index_fresh(layout, doc_id) is True
    assert ensure_index_fresh(layout, doc_id) is False
    meta = index_meta(layout, doc_id)
    assert meta and meta["source_hash"]

    queries._FRESH_OK.discard(doc_id)
    got = queries.search_anchors(layout, doc_id, "DIRECT-EDIT-MARKER")
    assert any("DIRECT-EDIT-MARKER" in (r.get("content") or "") for r in got)


def test_blocks_only_fast_path_keeps_fingerprint_stable(sample_doc):
    """块编辑快路径不得把指纹写成部分值——否则每次进程重启都误判失配全量重建。"""
    layout, doc_id = sample_doc
    reindex_doc(layout, doc_id)                    # 全量：两段齐全
    reindex_doc(layout, doc_id, blocks_only=True)  # 块编辑热路径（数据未变）
    assert ensure_index_fresh(layout, doc_id) is False, \
        "blocks_only 后指纹必须仍与全量一致（锚点段保留）"


def test_fingerprint_is_two_part():
    a = [{"anchor_id": "x", "content": "c", "text_hash": "h"}]
    b = [{"id": "b", "revision": 1, "title": "t"}]
    full = source_fingerprint(a, b)
    assert "|" in full
    assert full == anchors_fingerprint(a) + "|" + blocks_fingerprint(b)


def test_block_detail_sees_direct_edit(sample_doc):
    layout, doc_id = sample_doc
    # 夹具只建锚点（块由 generate 步创建）：手工放一个最小块行
    ch = json.loads((layout.data_dir / doc_id / "manifest.json").read_text(encoding="utf-8"))["chapters"][0]["chapter_id"]
    bpath = layout.blocks(doc_id, ch)
    row = {"id": "b-direct-edit-0000", "doc_id": doc_id, "chapter_id": ch,
           "type": "concept", "title": "OLD-TITLE", "status": "draft", "order": 1.0,
           "revision": 1, "content": "c", "sources": [], "links": []}
    bpath.parent.mkdir(parents=True, exist_ok=True)
    bpath.write_text(json.dumps(row, ensure_ascii=False) + NL, encoding="utf-8")
    reindex_doc(layout, doc_id)

    row["title"] = "DIRECT-EDIT-BLOCK-TITLE"
    bpath.write_text(json.dumps(row, ensure_ascii=False) + NL, encoding="utf-8")
    queries._FRESH_OK.discard(doc_id)
    got = queries.block_detail(layout, doc_id, row["id"])
    assert got and got.get("title") == "DIRECT-EDIT-BLOCK-TITLE"
