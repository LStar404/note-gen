"""doc_map 回归：块编辑快路径（blocks_only）不得清掉锚点→文档 映射。

历史现场：4 本书的 doc_map 里锚点映射全为 0（锚点却有数千个），于是每次
「点来源跳转」都要回退遍历所有书的锚点文件——正确性靠兜底扫描保住，但慢。
"""

from __future__ import annotations

from src.core.store import DocStore
from src.db.reindex import reindex_doc
from src.db.runtime import get_runtime


def test_blocks_only_reindex_keeps_anchor_mappings(sample_doc):
    layout, doc_id = sample_doc
    store = DocStore(layout, doc_id)
    chapter_id = store.chapter_ids()[0]
    anchors = store.anchor_rows(chapter_id)
    assert anchors, "样本书需要至少一个锚点"
    aid = anchors[0]["anchor_id"]
    rt = get_runtime(layout.runtime_db)

    reindex_doc(layout, doc_id)                      # 全量：两种映射都写
    assert rt.lookup_doc("anchor", aid) == doc_id
    assert rt.lookup_doc("block", store.block_rows(chapter_id)[0]["id"]) if store.block_rows(chapter_id) else True

    # 模拟一次「块编辑」触发的快路径重建：锚点未变，映射必须保留
    reindex_doc(layout, doc_id, blocks_only=True)
    assert rt.lookup_doc("anchor", aid) == doc_id, \
        "块编辑快路径把锚点映射清空了（曾致 4 本书映射为 0，锚点查询退化为全库扫描）"


def test_integrity_report_flags_missing_doc_map(sample_doc):
    """体检脚本的告警必须真能亮（映射丢了要报出来，否则等于没查）。"""
    from scripts.integrity_report import _doc_map_ok

    layout, doc_id = sample_doc
    rt = get_runtime(layout.runtime_db)
    reindex_doc(layout, doc_id)
    assert _doc_map_ok(layout, DocStore(layout, doc_id)) is True
    rt.forget_doc(doc_id)                      # 模拟映射被清掉
    assert _doc_map_ok(layout, DocStore(layout, doc_id)) is False
