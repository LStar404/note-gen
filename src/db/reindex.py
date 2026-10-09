"""由主数据重建 SQLite 派生索引（附录 B）；reindex 只重建派生表，不修复 JSONL 反向索引。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from src.core.jsonl import canonical_json
from src.core.paths import Layout
from src.core.store import DocStore

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    return conn


def reindex_doc(layout: Layout, doc_id: str) -> dict:
    """锁内从已提交主数据重建派生表，并在同一事务内更新 index_meta。"""
    store = DocStore(layout, doc_id)
    manifest = store.manifest()
    db_path = layout.index_db(doc_id)
    conn = _connect(db_path)
    try:
        with conn:
            for table in ("anchor_blocks", "block_links", "blocks", "anchors",
                          "chapters", "documents"):
                conn.execute(f"DELETE FROM {table}")
            try:
                conn.execute("INSERT INTO anchors_fts(anchors_fts) VALUES('delete-all')")
            except sqlite3.OperationalError:
                pass

            conn.execute(
                "INSERT INTO documents(doc_id,title,pdf_sha256,parser_name,schema_version)"
                " VALUES(?,?,?,?,?)",
                (manifest["doc_id"], manifest["title"], manifest["pdf_sha256"],
                 manifest["parser"]["name"], manifest["schema_version"]))
            for ch in manifest.get("chapters", []):
                conn.execute("INSERT INTO chapters(doc_id,chapter_id,title,page_start,page_end)"
                             " VALUES(?,?,?,?,?)",
                             (doc_id, ch["chapter_id"], ch["title"], ch["page_start"], ch["page_end"]))

            anchors = store.all_anchor_rows()
            for a in anchors:
                conn.execute(
                    "INSERT OR REPLACE INTO anchors(anchor_id,doc_id,chapter_id,page,bbox_x0,bbox_y0,"
                    "bbox_x1,bbox_y1,content_type,reading_order,heading_level,content)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (a["anchor_id"], doc_id, a["chapter_id"], a["page"],
                     float(a["bbox"][0]), float(a["bbox"][1]), float(a["bbox"][2]), float(a["bbox"][3]),
                     a["content_type"], a["reading_order"], a.get("heading_level", 0), a["content"]))
                for bid in a.get("note_block_ids", []):
                    conn.execute("INSERT OR IGNORE INTO anchor_blocks(anchor_id,block_id) VALUES(?,?)",
                                 (a["anchor_id"], bid))

            blocks = store.all_block_rows(include_deleted=True)
            for b in blocks:
                conn.execute(
                    "INSERT OR REPLACE INTO blocks(block_id,doc_id,chapter_id,type,title,status,"
                    '"order",revision,generation_protected,deleted_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (b["id"], doc_id, b["chapter_id"], b["type"], b["title"], b["status"],
                     float(b["order"]), int(b["revision"]),
                     1 if b.get("generation_protected") else 0, b.get("deleted_at")))
                for ln in b.get("links", []):
                    if ln.startswith("http://") or ln.startswith("https://") or ln.startswith("ref_"):
                        continue
                    conn.execute("INSERT OR IGNORE INTO block_links(from_block_id,to_block_id) VALUES(?,?)",
                                 (b["id"], ln))

            try:
                conn.execute("INSERT INTO anchors_fts(anchors_fts) VALUES('rebuild')")
            except sqlite3.OperationalError:
                for a in anchors:
                    conn.execute("INSERT INTO anchors_fts(rowid, content) SELECT rowid, content FROM anchors WHERE anchor_id=?",
                                 (a["anchor_id"],))
            source_hash = canonical_json({
                "anchors": len(anchors), "blocks": len(blocks),
                "revision": manifest.get("data_revision")})
            import hashlib
            conn.execute("INSERT OR REPLACE INTO index_meta(singleton,source_revision,source_hash)"
                         " VALUES(1,?,?)",
                         (int(manifest.get("data_revision", 0)),
                          hashlib.sha256(source_hash.encode("utf-8")).hexdigest()))
        return {"doc_id": doc_id, "anchors": len(anchors), "blocks": len(blocks)}
    finally:
        conn.close()


def index_meta(layout: Layout, doc_id: str) -> dict | None:
    db_path = layout.index_db(doc_id)
    if not db_path.exists():
        return None
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT * FROM index_meta WHERE singleton=1").fetchone()
        return dict(row) if row else None
    finally:
        conn.close()
