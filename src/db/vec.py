"""sqlite-vec 语义向量层（附录 B vec_anchors）：向量表重建与 Top-K 检索。"""

from __future__ import annotations

import sqlite3

from src.core.paths import Layout

DEFAULT_DIM = 1024
DEFAULT_THRESHOLD = 0.55   # cosine 相似度阈值（6.3.3）


def vec_available() -> bool:
    try:
        import sqlite_vec  # noqa: F401

        return True
    except Exception:
        return False


def _load_vec(conn: sqlite3.Connection) -> bool:
    try:
        import sqlite_vec

        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        return True
    except Exception:
        return False


def ensure_vec_table(conn: sqlite3.Connection, dim: int) -> bool:
    """建 vec_anchors（维度在建表时固定；维度变化时重建）。"""
    if not _load_vec(conn):
        return False
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='vec_anchors'").fetchone()
    if row:
        cols = conn.execute("PRAGMA table_info(vec_anchors)").fetchall()
        current_dim = None
        for col in cols:
            if col[1] == "embedding":
                m = __import__("re").search(r"float\[(\d+)\]", (col[2] or "").lower())
                current_dim = int(m.group(1)) if m else None
        if current_dim != dim:
            conn.execute("DROP TABLE vec_anchors")
            row = None
    if not row:
        conn.execute(
            f"CREATE VIRTUAL TABLE vec_anchors USING vec0(anchor_id TEXT PRIMARY KEY, "
            f"embedding float[{int(dim)}])")
    return True


def rebuild_vectors(conn: sqlite3.Connection, anchors: list[dict], dim: int) -> int:
    """从锚点 embedding 字段重建向量表；无向量的锚点跳过。"""
    if not ensure_vec_table(conn, dim):
        return 0
    conn.execute("DELETE FROM vec_anchors")
    import sqlite_vec

    count = 0
    for a in anchors:
        emb = a.get("embedding")
        if not emb:
            continue
        if len(emb) != dim:
            continue
        conn.execute("INSERT OR REPLACE INTO vec_anchors(anchor_id, embedding) VALUES(?, ?)",
                     (a["anchor_id"], sqlite_vec.serialize_float32(list(map(float, emb)))))
        count += 1
    return count


def search_vec(layout: Layout, doc_id: str, query_vec: list[float], *, k: int = 8,
               threshold: float = DEFAULT_THRESHOLD) -> list[dict]:
    """Top-K 余弦相似度检索（6.3.3）：cosine ≥ threshold。"""
    db_path = layout.index_db(doc_id)
    if not db_path.exists() or not query_vec:
        return []
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        if not _load_vec(conn):
            return []
        import sqlite_vec

        rows = conn.execute(
            "SELECT v.anchor_id, v.distance FROM vec_anchors v "
            "WHERE v.embedding MATCH ? AND k = ? ORDER BY v.distance",
            (sqlite_vec.serialize_float32(list(map(float, query_vec))), int(k)),
        ).fetchall()
        out = []
        for r in rows:
            similarity = 1.0 - float(r["distance"])
            if similarity < threshold:
                continue
            detail = conn.execute("SELECT * FROM anchors WHERE anchor_id=?",
                                  (r["anchor_id"],)).fetchone()
            if detail:
                out.append({**dict(detail), "similarity": similarity, "match": "vector"})
        return out
    finally:
        conn.close()
