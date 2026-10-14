"""Agent 工具参数校验回归：缺字段要说人话，不得 KeyError→500 或抛出 None。"""

from __future__ import annotations

import pytest

from src.api.agent_edit import check_scope, run_tool, validate_args
from src.core.errors import InputError
from src.core.store import DocStore


def _seed(store: DocStore, ch: str) -> dict:
    return store.create_block({"type": "motivation", "title": "块", "content": "内容。",
                               "sources": [], "basis": "synthesis", "order": 1.0},
                              author="human", chapter_id=ch)


def test_validate_args_rejects_missing_and_blank():
    with pytest.raises(InputError) as ei:
        validate_args("update_block", {})
    assert "block_id" in str(ei.value) and "update_block" in str(ei.value)
    with pytest.raises(InputError):
        validate_args("update_block", {"block_id": "   "})     # 空白也算缺
    with pytest.raises(InputError):
        validate_args("link_blocks", {"from_block_id": "b_x"})  # 少 to_block_id
    validate_args("create_block", {"chapter_id": "ch01", "type": "link", "title": "t",
                                   "content": "c"})             # 齐全不报错


def test_run_tool_missing_block_id_gives_readable_error(sample_doc):
    """曾经：args["block_id"] 缺字段 → KeyError→500；args.get 的 None 流进 store →
    界面出现「块不存在: None」。现在统一是 InputError。"""
    layout, doc_id = sample_doc
    store = DocStore(layout, doc_id)
    with pytest.raises(InputError, match="block_id"):
        run_tool(store, "update_block", {"content": "x"}, author="agent")


def test_check_scope_missing_block_id_does_not_probe_store(sample_doc):
    layout, doc_id = sample_doc
    store = DocStore(layout, doc_id)
    ch = store.chapter_ids()[0]
    _seed(store, ch)
    scope = {"kind": "chapter", "chapter_id": ch}
    with pytest.raises(InputError, match="block_id"):
        check_scope(store, scope, "delete_block", {})
    with pytest.raises(InputError, match="from_block_id"):
        check_scope(store, scope, "link_blocks", {"to_block_id": "b_y"})
