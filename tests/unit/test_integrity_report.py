"""完整性体检脚本回归：锚点身份判定、引文最近片段、dry-run 不写盘。"""

from __future__ import annotations


from scripts.integrity_report import anchor_id_mismatch, find_best_quote, fix_quotes, scan_doc
from src.core.store import DocStore
from src.core.text import base_anchor_id, normalize_text, text_hash


def test_anchor_id_mismatch_ignores_dedup_suffix():
    c = "定理 1.1 内容"
    ok = {"anchor_id": base_anchor_id("doc-x", c), "text_hash": text_hash(c)}
    assert not anchor_id_mismatch(ok)
    dup = {"anchor_id": base_anchor_id("doc-x", c) + "-2", "text_hash": text_hash(c)}
    assert not anchor_id_mismatch(dup), "去重后缀 -2 属于正常实例"
    stale = {"anchor_id": "a_deadbeef_0000000000000000", "text_hash": text_hash(c)}
    assert anchor_id_mismatch(stale)


def test_find_best_quote_returns_real_substring():
    content = "设 f 在区域 D 内全纯，则 f 的积分表示为留数之和。"
    # 旧引文被改写/OCR 噪声化：修复结果必须仍是锚点内容的连续子串
    for old in ("设 f 在全纯区域 D 内解析", "", "totally different text"):
        got = find_best_quote(content, old)
        assert got and normalize_text(got) in normalize_text(content), (old, got)
        assert len(got) <= 80


def test_scan_and_dry_run_do_not_mutate(sample_doc):
    layout, doc_id = sample_doc
    store = DocStore(layout, doc_id)
    ch = store.chapter_ids()[0]
    block = store.create_block({"type": "motivation", "title": "块", "content": "内容。",
                                "sources": [], "basis": "synthesis", "order": 1.0},
                               author="human", chapter_id=ch)
    st = scan_doc(store)
    assert st["anchors"] >= 0 and st["missing"] == []
    fixed, skipped = fix_quotes(store, [(block["id"], "a_nope", "old", "new")], apply=False)
    assert (fixed, skipped) == (0, 1), "锚点不在块的 sources 里时必须跳过"
    assert DocStore(layout, doc_id).get_block(block["id"])["revision"] == block["revision"], \
        "dry-run 不得改数据"
