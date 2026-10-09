"""5.7 合并策略红线（E-13/E-22）：人工成果永不覆盖、完整提纲才允许删旧块。"""

from __future__ import annotations

from src.core.store import DocStore
from src.generate.merge import identity_key, merge_candidates


def _anchor_ref(store: DocStore):
    anchor = store.anchor_rows("ch01")[1]
    return anchor["anchor_id"], anchor["content"][:12]


def _candidate(temp_id: str, type_: str, title: str, ref, *, content: str = "内容",
               quote: str | None = None):
    anchor_id = ref[0] if ref else None
    return {"temp_id": temp_id, "type": type_, "title": title,
            "basis": "anchored" if anchor_id else "synthesis",
            "sources": ([{"anchor_id": anchor_id, "quote": quote or ref[1], "relation": "defines"}]
                        if anchor_id else []),
            "depends_on": [], "order": 10.0, "content": content,
            "content_format": "md_latex", "tags": [], "status": "ai_generated", "_status": "ok"}


def _make_block(store: DocStore, ref, *, title: str = "定义", author: str = "ai",
                content: str = "内容"):
    return store.create_block(
        {"type": "definition", "title": title, "content": content,
         "sources": [{"anchor_id": ref[0], "quote": ref[1], "relation": "defines"}],
         "order": 10.0, "status": "ai_generated"},
        author=author, chapter_id="ch01")


def test_protected_block_never_overwritten(store: DocStore):
    ref = _anchor_ref(store)
    block = _make_block(store, ref, author="human")
    cand = _candidate("t1", "definition", "定义", ref, content="完全不同的内容")
    mutation, report = merge_candidates(store, "ch01", [cand])
    assert report["skipped_protected"] == [block["id"]]
    assert block["id"] not in report["updated"] and block["id"] not in report["created"]


def test_identical_candidate_skipped(store: DocStore):
    ref = _anchor_ref(store)
    block = _make_block(store, ref)
    cand = _candidate("t1", "definition", "定义", ref, content="内容")
    mutation, report = merge_candidates(store, "ch01", [cand])
    assert report["skipped_identical"] == [block["id"]]
    assert not mutation.history            # 不写历史、不 revision+1


def test_changed_candidate_replaces_in_place(store: DocStore):
    ref = _anchor_ref(store)
    block = _make_block(store, ref)
    cand = _candidate("t1", "definition", "定义", ref, content="新的内容")
    mutation, report = merge_candidates(store, "ch01", [cand])
    assert report["updated"] == [block["id"]]
    rows = mutation.blocks["ch01"]
    updated = next(r for r in rows if r["id"] == block["id"])
    assert updated["content"] == "新的内容"
    assert updated["revision"] == block["revision"] + 1
    assert len(mutation.history) == 1


def test_new_candidate_gets_new_id(store: DocStore):
    ref = _anchor_ref(store)
    cand = _candidate("t1", "definition", "全新定义", ref)
    mutation, report = merge_candidates(store, "ch01", [cand])
    assert len(report["created"]) == 1
    assert report["created"][0].startswith("b_")


def test_protected_tombstone_suppresses_revival(store: DocStore):
    ref = _anchor_ref(store)
    block = _make_block(store, ref, author="human")
    store.soft_delete_block(block["id"], author="human")
    cand = _candidate("t1", "definition", "定义", ref)
    mutation, report = merge_candidates(store, "ch01", [cand])
    assert report["suppressed_deleted"] == [block["id"]]
    assert not report["created"]


def test_unprotected_tombstone_restores_id(store: DocStore):
    ref = _anchor_ref(store)
    block = _make_block(store, ref)
    store.soft_delete_block(block["id"], author="ai")
    cand = _candidate("t1", "definition", "定义", ref)
    mutation, report = merge_candidates(store, "ch01", [cand])
    assert report["restored"] == [block["id"]]


def test_removal_requires_complete_outline(store: DocStore):
    ref = _anchor_ref(store)
    block = _make_block(store, ref)
    cand = _candidate("t1", "motivation", "动机", None)
    cand_fail = dict(cand, temp_id="t2", _status="failed", _errors=["失败"])
    mutation, report = merge_candidates(store, "ch01", [cand, cand_fail])
    assert block["id"] not in report["deleted"]      # fill 失败不得误删旧块
    assert report["failures"]
    mutation, report = merge_candidates(store, "ch01", [cand], outline_complete=True)
    assert report["deleted"] == [block["id"]]        # 完整提纲明确排除才软删


def test_identity_key():
    a = {"type": "definition", "sources": [{"anchor_id": "a2"}, {"anchor_id": "a1"}], "title": "T"}
    b = {"type": "definition", "sources": [{"anchor_id": "a1"}, {"anchor_id": "a2"}], "title": "T"}
    assert identity_key(a) == identity_key(b)
    c = {"type": "motivation", "sources": [], "title": "  A  b "}
    d = {"type": "motivation", "sources": [], "title": "A b"}
    assert identity_key(c) == identity_key(d)
