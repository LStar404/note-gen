"""agent scope 越界回归（9.4）：块级不得夹带 create_block / 跨章 link。"""

from __future__ import annotations

import pytest

from src.api.agent_edit import check_scope
from src.core.errors import SchemaViolation


def test_create_block_rejected_in_block_scope(store):
    scope = {"kind": "block", "block_id": "b_x", "chapter_id": "ch01"}
    with pytest.raises(SchemaViolation) as ei:
        check_scope(store, scope, "create_block",
                    {"chapter_id": "ch01", "type": "definition", "title": "t",
                     "content": "c", "message": "m"})
    assert ei.value.code == "SCOPE_VIOLATION"


def test_create_block_allowed_in_chapter_scope(store):
    scope = {"kind": "chapter", "block_id": None, "chapter_id": "ch01"}
    check_scope(store, scope, "create_block",
                {"chapter_id": "ch01", "type": "definition", "title": "t",
                 "content": "c", "message": "m"})


def test_link_blocks_cross_chapter_rejected(store):
    chapters = store.chapter_ids()
    assert len(chapters) >= 2, f"黄金样本应有至少两章: {chapters}"
    scope = {"kind": "chapter", "block_id": None, "chapter_id": chapters[0]}
    # from 与 to 都真实存在但属于另一章：章级 scope 不得改到别的章的块
    b1 = store.create_block({"type": "motivation", "title": "a", "content": "x",
                             "sources": [], "basis": "synthesis", "order": 1.0},
                            author="tester", chapter_id=chapters[1])
    b2 = store.create_block({"type": "motivation", "title": "b", "content": "y",
                             "sources": [], "basis": "synthesis", "order": 2.0},
                            author="tester", chapter_id=chapters[1])
    with pytest.raises(SchemaViolation) as ei:
        check_scope(store, scope, "link_blocks",
                    {"from_block_id": b1["id"], "to_block_id": b2["id"], "message": "m"})
    assert ei.value.code == "SCOPE_VIOLATION"
