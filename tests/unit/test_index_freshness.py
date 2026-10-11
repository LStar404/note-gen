"""派生索引新鲜度回归：直改 JSONL（离线修复路径）后检索/锚点面板不得返回旧内容。"""

from __future__ import annotations

import glob
import json

from src.db import queries
from src.db.reindex import ensure_index_fresh, index_meta, reindex_doc, source_fingerprint

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
