"""索引查询（6.4）：按页查锚点/块、点查、选区候选、FTS 兜底。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from src.core.coord import match_scores
from src.core.paths import Layout
from src.core.text import search_normalize

# 派生索引新鲜度守护：每进程每文档校验一次（离线修复直改 JSONL 后索引静默滞后，
# 检索/锚点面板会返回旧内容）。进程重启即重新校验，与 store 行缓存失效模型一致。
_FRESH_OK: set[str] = set()


def _ensure_index(layout: Layout, doc_id: str) -> None:
    if doc_id in _FRESH_OK:
        return
    try:
        from src.db.reindex import ensure_index_fresh

        ensure_index_fresh(layout, doc_id)
    except Exception:
        return            # 索引滞后/重建失败不阻塞读取（4.2.2：派生层尽力而为）
    _FRESH_OK.add(doc_id)


def _connect(db_path: Path) -> sqlite3.Connection | None:
    if not Path(db_path).exists():
        return None
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def anchors_on_page(layout: Layout, doc_id: str, page: int) -> list[dict]:
    _ensure_index(layout, doc_id)
    conn = _connect(layout.index_db(doc_id))
    if conn is None:
        return []
    try:
        rows = conn.execute("SELECT * FROM anchors WHERE doc_id=? AND page=? ORDER BY reading_order",
                            (doc_id, page)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def blocks_for_page(layout: Layout, doc_id: str, page: int) -> list[dict]:
    """教材→笔记：该页锚点 → note_block_ids → 块摘要。"""
    _ensure_index(layout, doc_id)
    conn = _connect(layout.index_db(doc_id))
    if conn is None:
        return []
    try:
        rows = conn.execute(
            "SELECT DISTINCT b.block_id, b.type, b.title, b.status FROM blocks b "
            "JOIN anchor_blocks ab ON ab.block_id=b.block_id "
            "JOIN anchors a ON a.anchor_id=ab.anchor_id "
            "WHERE a.doc_id=? AND a.page=? AND b.deleted_at IS NULL",
            (doc_id, page)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def anchor_detail(layout: Layout, doc_id: str, anchor_id: str) -> dict | None:
    _ensure_index(layout, doc_id)
    conn = _connect(layout.index_db(doc_id))
    if conn is None:
        return None
    try:
        row = conn.execute("SELECT * FROM anchors WHERE anchor_id=?", (anchor_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def block_detail(layout: Layout, doc_id: str, block_id: str) -> dict | None:
    conn = _connect(layout.index_db(doc_id))
    if conn is None:
        return None
    try:
        row = conn.execute("SELECT * FROM blocks WHERE block_id=?", (block_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def search_anchors(layout: Layout, doc_id: str, query: str, limit: int = 8) -> list[dict]:
    """FTS5 兜底；中文 trigram 不可用时退化 LIKE（附录 B）。"""
    _ensure_index(layout, doc_id)
    conn = _connect(layout.index_db(doc_id))
    if conn is None:
        return []
    try:
        try:
            rows = conn.execute(
                "SELECT a.* FROM anchors a JOIN anchors_fts f ON f.rowid=a.rowid "
                "WHERE anchors_fts MATCH ? LIMIT ?", (query, limit)).fetchall()
            if rows:
                return [dict(r) for r in rows]
        except sqlite3.OperationalError:
            pass
        rows = conn.execute("SELECT * FROM anchors WHERE doc_id=? AND content LIKE ? LIMIT ?",
                            (doc_id, f"%{query}%", limit)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def search_semantic(layout: Layout, doc_id: str, query_vec: list[float], *, k: int = 8,
                    threshold: float = 0.55) -> list[dict]:
    """语义兜底（6.3.3）：sqlite-vec Top-K，cosine ≥ threshold；不可用时返回空由 FTS 兜底。"""
    from src.db.vec import search_vec

    return search_vec(layout, doc_id, query_vec, k=k, threshold=threshold)


def match_selection(layout: Layout, doc_id: str, page: int, rects: list[list[float]],
                    text: str, config: dict) -> list[dict]:
    """6.3.2 精确命中算法：cover_anchor≥0.8 或 cover_selection≥0.5；文本辅助。"""
    contain = float(config.get("match", {}).get("contain_ratio", 0.8))
    cover = float(config.get("match", {}).get("cover_ratio", 0.5))
    top_k = int(config.get("match", {}).get("top_k", 8))
    if not rects:
        return []
    sel = (min(r[0] for r in rects), min(r[1] for r in rects),
           max(r[2] for r in rects), max(r[3] for r in rects))
    hits = []
    for a in anchors_on_page(layout, doc_id, page):
        bbox = (a["bbox_x0"], a["bbox_y0"], a["bbox_x1"], a["bbox_y1"])
        scores = match_scores(sel, bbox)
        geo_hit = scores["cover_anchor"] >= contain or scores["cover_selection"] >= cover
        text_hit = False
        if text and sel[2] > sel[0]:
            s_norm = search_normalize(text)
            a_norm = search_normalize(a.get("content", ""))
            if s_norm and s_norm in a_norm:
                min_len = 4 if any("\u4e00" <= ch <= "\u9fff" for ch in s_norm) else 8
                text_hit = len(s_norm) >= min_len
        if geo_hit or text_hit:
            hits.append({**a, "scores": scores, "match": "geometry" if geo_hit else "text"})
    hits.sort(key=lambda h: (-h["scores"]["cover_selection"], -h["scores"]["cover_anchor"],
                             -h["scores"]["iou"], h["reading_order"]))
    return hits[:max(10, top_k)][:10]
