"""增量索引与定向校验回归：优化不能牺牲正确性。

锁住三件事：
1) 单块编辑只重刷本章索引，其他章的行一行不少、映射仍在；
2) doc_map 定向同步后，其他章的 block→doc 映射不丢；
3) changed_block_ids 真的只校验本次改动的块（不再整章跑 pydantic）。
"""

from __future__ import annotations

import sqlite3

from src.core.store import DocStore, DocMutation
from src.db.reindex import reindex_doc
from src.db.runtime import get_runtime


def _count_rows(db_path, sql: str, params=()) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(sql, params).fetchone()[0]
    finally:
        conn.close()


def _mk_block(store: DocStore, ch: str, title: str, order: float) -> dict:
    return store.create_block({"type": "motivation", "title": title, "content": "内容。",
                               "sources": [], "basis": "synthesis", "order": order},
                              author="human", chapter_id=ch)


def test_block_edit_keeps_other_chapters_index_rows(sample_doc):
    layout, doc_id = sample_doc
    store = DocStore(layout, doc_id)
    chapters = store.chapter_ids()
    assert len(chapters) >= 2, "夹具需要至少两章"
    _mk_block(store, chapters[0], "甲", 1.0)
    _mk_block(store, chapters[1], "乙", 1.0)
    reindex_doc(layout, doc_id)                       # 全量建好
    db = layout.index_db(doc_id)
    before = {ch: _count_rows(db, "SELECT COUNT(*) FROM blocks WHERE chapter_id=?", (ch,))
              for ch in chapters}
    assert sum(before.values()) > 0

    # 在第 0 章建一个块（走定向提交 → 触发 scoped reindex）
    store.create_block({"type": "motivation", "title": "增量测试", "content": "内容。",
                        "sources": [], "basis": "synthesis", "order": 1.0},
                       author="human", chapter_id=chapters[0])
    after = {ch: _count_rows(db, "SELECT COUNT(*) FROM blocks WHERE chapter_id=?", (ch,))
             for ch in chapters}
    assert after[chapters[0]] == before[chapters[0]] + 1, "本章应新增一行"
    for ch in chapters[1:]:
        assert after[ch] == before[ch], f"其他章 {ch} 的索引行被动了"


def test_scoped_reindex_keeps_other_chapter_block_mappings(sample_doc):
    layout, doc_id = sample_doc
    store = DocStore(layout, doc_id)
    chapters = store.chapter_ids()
    rt = get_runtime(layout.runtime_db)
    other = chapters[1]
    other_block = _mk_block(store, other, "乙", 1.0)["id"]
    reindex_doc(layout, doc_id)
    assert rt.lookup_doc("block", other_block) == doc_id

    reindex_doc(layout, doc_id, blocks_only=True, chapters=[chapters[0]])
    assert rt.lookup_doc("block", other_block) == doc_id, \
        "定向 reindex 不得清掉其他章的 block→doc 映射"


def test_changed_block_ids_limits_schema_validation(sample_doc):
    """只校验本次改动的块：另一块即便在磁盘上被写坏，也不影响定向提交。"""
    layout, doc_id = sample_doc
    store = DocStore(layout, doc_id)
    ch = store.chapter_ids()[0]
    good = store.create_block({"type": "motivation", "title": "好块", "content": "内容。",
                               "sources": [], "basis": "synthesis", "order": 1.0},
                              author="human", chapter_id=ch)
    bad = store.create_block({"type": "motivation", "title": "坏块", "content": "内容。",
                              "sources": [], "basis": "synthesis", "order": 2.0},
                             author="human", chapter_id=ch)
    rows = [dict(b) for b in store.block_rows(ch, include_deleted=True)]
    rows = [{**b, "type": "不存在的类型"} if b["id"] == bad["id"] else b for b in rows]
    store.commit(DocMutation(blocks={ch: rows}, changed_block_ids={good["id"]}),
                 base_revision=store.data_revision(), validate=True)
    # 全量校验仍能抓到那块坏数据（说明坏块确实被写进去了）
    from src.core.errors import SchemaViolation
    try:
        store.integrity_check({}, changed_rows_only=False)
    except SchemaViolation:
        pass
    else:
        raise AssertionError("全量校验应当发现坏块")
