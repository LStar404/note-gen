"""由主数据重建 SQLite 派生索引（附录 B）；reindex 只重建派生表，不修复 JSONL 反向索引。"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from src.core.jsonl import canonical_json
from src.core.paths import Layout
from src.core.store import DocStore
from src.db.vec import DEFAULT_DIM, rebuild_vectors

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def _sha(payload) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def anchors_fingerprint(anchors: list[dict]) -> str:
    return _sha([[a.get("anchor_id"), a.get("content"), a.get("text_hash")]
                 for a in sorted(anchors, key=lambda x: x["anchor_id"])])


def blocks_fingerprint(blocks: list[dict]) -> str:
    # 块的 sources 决定 anchor_blocks 派生表，同样纳入指纹
    return _sha([[b.get("id"), b.get("revision"), b.get("title"), b.get("status"),
                  b.get("order"), b.get("deleted_at"),
                  [s.get("anchor_id") for s in b.get("sources") or []]]
                 for b in sorted(blocks, key=lambda x: x["id"])])


def source_fingerprint(anchors: list[dict], blocks: list[dict]) -> str:
    """主数据内容指纹：以 content 为准（text_hash 可能滞后于直改内容）。

    只计数/revision 的旧指纹对「直接改写 JSONL 的离线修复」视而不见，
    索引静默滞后、检索与锚点面板返回旧乱码——指纹必须覆盖内容本身。
    两段式「锚点段|块段」：blocks_only 快路径只换块段、保留锚点段，
    否则快路径写出的部分指纹会让每次进程重启都误判失配、全量重建。
    """
    return anchors_fingerprint(anchors) + "|" + blocks_fingerprint(blocks)


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    return conn


def reindex_doc(layout: Layout, doc_id: str, vec_dim: int | None = None,
                *, blocks_only: bool = False) -> dict:
    """锁内从已提交主数据重建派生表，并在同一事务内更新 index_meta。

    blocks_only=True 走快路径：只刷新 blocks/anchor_blocks/block_links（块编辑热路径），
    不重建 anchors 与 FTS（锚点未变）；否则全量重建（含 sqlite-vec 向量表）。
    """
    store = DocStore(layout, doc_id)
    manifest = store.manifest()
    db_path = layout.index_db(doc_id)
    conn = _connect(db_path)
    try:
        with conn:
            tables = ("anchor_blocks", "block_links", "blocks") if blocks_only else (
                "anchor_blocks", "block_links", "blocks", "anchors", "chapters", "documents")
            for table in tables:
                conn.execute(f"DELETE FROM {table}")
            try:
                conn.execute("INSERT INTO anchors_fts(anchors_fts) VALUES('delete-all')")
            except sqlite3.OperationalError:
                pass

            if not blocks_only:
                conn.execute(
                    "INSERT INTO documents(doc_id,title,pdf_sha256,parser_name,schema_version)"
                    " VALUES(?,?,?,?,?)",
                    (manifest["doc_id"], manifest["title"], manifest["pdf_sha256"],
                     manifest["parser"]["name"], manifest["schema_version"]))
                for ch in manifest.get("chapters", []):
                    conn.execute("INSERT INTO chapters(doc_id,chapter_id,title,page_start,page_end)"
                                 " VALUES(?,?,?,?,?)",
                                 (doc_id, ch["chapter_id"], ch["title"], ch["page_start"], ch["page_end"]))

            anchors = [] if blocks_only else store.all_anchor_rows()
            for a in anchors:
                conn.execute(
                    "INSERT OR REPLACE INTO anchors(anchor_id,doc_id,chapter_id,page,bbox_x0,bbox_y0,"
                    "bbox_x1,bbox_y1,content_type,reading_order,heading_level,content)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (a["anchor_id"], doc_id, a["chapter_id"], a["page"],
                     float(a["bbox"][0]), float(a["bbox"][1]), float(a["bbox"][2]), float(a["bbox"][3]),
                     a["content_type"], a["reading_order"], a.get("heading_level", 0), a["content"]))
                _ = a  # anchor_blocks 由块的 sources 派生（见下），快路径不依赖锚点扫描

            blocks = store.all_block_rows(include_deleted=True)
            for b in blocks:
                conn.execute(
                    "INSERT OR REPLACE INTO blocks(block_id,doc_id,chapter_id,type,title,status,"
                    '"order",revision,generation_protected,deleted_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (b["id"], doc_id, b["chapter_id"], b["type"], b["title"], b["status"],
                     float(b["order"]), int(b["revision"]),
                     1 if b.get("generation_protected") else 0, b.get("deleted_at")))
                # anchor_blocks 由块的 sources 派生（I3 下与 note_block_ids 等价），
                # 这样块编辑快路径无需扫描锚点文件
                if not b.get("deleted_at"):
                    for src in b.get("sources", []):
                        aid = src.get("anchor_id")
                        if aid:
                            conn.execute(
                                "INSERT OR IGNORE INTO anchor_blocks(anchor_id,block_id) VALUES(?,?)",
                                (aid, b["id"]))
                for ln in b.get("links", []):
                    if ln.startswith("http://") or ln.startswith("https://") or ln.startswith("ref_"):
                        continue
                    conn.execute("INSERT OR IGNORE INTO block_links(from_block_id,to_block_id) VALUES(?,?)",
                                 (b["id"], ln))

            # 语义向量层（v2）：从 anchors.embedding 重建（锚点未变的快路径跳过）
            vec_count = 0
            if blocks_only and _vec_table_exists(conn):
                try:
                    vec_count = conn.execute("SELECT COUNT(*) FROM vec_anchors").fetchone()[0]
                except Exception:
                    vec_count = 0        # vec0 扩展未加载：向量数不变，不必重建
            dim = vec_dim
            if dim is None:
                emb = next((a.get("embedding") for a in anchors if a.get("embedding")), None)
                dim = len(emb) if emb else DEFAULT_DIM
            if not blocks_only:
                vec_count = rebuild_vectors(conn, anchors, int(dim))

            try:
                if not blocks_only:
                    conn.execute("INSERT INTO anchors_fts(anchors_fts) VALUES('rebuild')")
            except sqlite3.OperationalError:
                for a in anchors:
                    conn.execute("INSERT INTO anchors_fts(rowid, content) SELECT rowid, content FROM anchors WHERE anchor_id=?",
                                 (a["anchor_id"],))
            # 点查映射同步（6.4：block_id/anchor_id -> doc_id，每次提交/重建时更新）
            try:
                from src.db.runtime import get_runtime

                get_runtime(layout.runtime_db).sync_doc_map(
                    doc_id,
                    [b["id"] for b in store.all_block_rows(include_deleted=True)],
                    # 快路径不扫锚点：传 None 表示「锚点未变，保留原映射」，
                    # 传空列表会把该书的锚点映射整片删掉（曾让 4 本书映射为 0）
                    None if blocks_only else [a["anchor_id"] for a in anchors])
            except Exception:
                pass

            fp_blocks = blocks_fingerprint(blocks)
            if blocks_only:
                # 锚点未变：保留已有锚点段（缺历史值时留空 → 下次校验判失配走全量，正确）
                old = conn.execute("SELECT source_hash FROM index_meta WHERE singleton=1").fetchone()
                anchors_part = ((old["source_hash"] or "").split("|")[0] if old else "")
            else:
                anchors_part = anchors_fingerprint(anchors)
            conn.execute("INSERT OR REPLACE INTO index_meta(singleton,source_revision,source_hash)"
                         " VALUES(1,?,?)",
                         (int(manifest.get("data_revision", 0)),
                          anchors_part + "|" + fp_blocks))
        return {"doc_id": doc_id, "anchors": len(anchors), "blocks": len(blocks),
                "vectors": vec_count}
    finally:
        conn.close()


def ensure_index_fresh(layout: Layout, doc_id: str) -> bool:
    """派生索引与主数据内容指纹比对，失配则全量重建（含 FTS/向量）。

    覆盖「绕过事务直接改写 JSONL」的漂移场景（离线修复/手工订正）：
    索引滞后时检索与锚点面板返回旧内容。返回是否发生重建。

    行缓存按 data_revision 键控，直改文件不升 revision——比对前必须先失效
    进程缓存，否则指纹读到陈旧行、漂移漏检（回归测试抓到的真 bug）。
    """
    DocStore._invalidate(layout, doc_id)
    store = DocStore(layout, doc_id)
    anchors = store.all_anchor_rows()
    blocks = store.all_block_rows(include_deleted=True)
    want = source_fingerprint(anchors, blocks)
    meta = index_meta(layout, doc_id)
    if meta and meta.get("source_hash") == want:
        return False
    reindex_doc(layout, doc_id)
    return True


def _vec_table_exists(conn: sqlite3.Connection) -> bool:
    try:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='vec_anchors'").fetchone() is not None
    except Exception:
        return False


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
