"""4.7 校验规则与块/锚点模型（schema 2）。"""

from __future__ import annotations

import pytest

from src.core.errors import AnchorNotFound, SchemaViolation
from src.core.models import (NoteBlock, ParagraphAnchor, block_snapshot, quote_matches,
                             validate_anchor, validate_block)

NOW = "2026-01-10T11:02:00Z"


def make_block(**kw) -> NoteBlock:
    base = dict(id="b_8x2kQ7pL3mNf", doc_id="demo-la", chapter_id="ch01", type="definition",
                title="行列式的定义", content="设 $A$ 为方阵", content_format="md_latex",
                order=10.0, heading_anchor_id=None, sources=[], links=[], status="ai_generated",
                basis="anchored", tags=[], created_by="ai", generation_protected=False,
                revision=1, created_at=NOW, updated_at=NOW, deleted_at=None)
    base.update(kw)
    return NoteBlock.model_validate(base)


def make_anchor(**kw) -> ParagraphAnchor:
    base = dict(anchor_id="a_92e41455_dc6f1a2453d5ef97", doc_id="demo-la", chapter_id="ch01",
                page=1, reading_order=0, bbox=[54.0, 700.0, 300.0, 730.0],
                text_hash="d" * 64, content="行列式的定义", content_type="paragraph",
                heading_level=0, embedding=None, continuation_of=None, note_block_ids=[])
    base.update(kw)
    return ParagraphAnchor.model_validate(base)


def test_block_schema_ok():
    src = {"anchor_id": "a_92e41455_dc6f1a2453d5ef97", "quote": "行列式", "relation": "defines"}
    validate_block(make_block(sources=[src]), anchor_ids={src["anchor_id"]})


def test_block_rejects_bad_ids_and_order():
    with pytest.raises(SchemaViolation):
        validate_block(make_block(id="bad"))
    with pytest.raises(SchemaViolation):
        validate_block(make_block(order=float("nan")))


def test_block_content_size_limit():
    with pytest.raises(SchemaViolation):
        validate_block(make_block(content="x" * (50 * 1024 + 1)))


def test_block_type_source_constraints():
    with pytest.raises(SchemaViolation):
        validate_block(make_block(type="definition", sources=[]))     # 必须有来源
    with pytest.raises(SchemaViolation):
        validate_block(make_block(type="motivation", sources=[], basis="anchored"))
    validate_block(make_block(type="motivation", sources=[], basis="synthesis"))
    with pytest.raises(SchemaViolation):
        validate_block(make_block(type="theorem", basis="synthesis", sources=[]))


def test_block_source_anchor_must_exist():
    src = {"anchor_id": "a_92e41455_dc6f1a2453d5ef97", "quote": "行列式", "relation": "defines"}
    with pytest.raises(AnchorNotFound):
        validate_block(make_block(sources=[src]), anchor_ids=set())
    validate_block(make_block(sources=[src]), anchor_ids={src["anchor_id"]})


def test_block_duplicate_source_anchor_rejected():
    src = {"anchor_id": "a_92e41455_dc6f1a2453d5ef97", "quote": "行列式", "relation": "defines"}
    with pytest.raises(SchemaViolation):
        validate_block(make_block(sources=[src, dict(src)]),
                       anchor_ids={src["anchor_id"]})


def test_anchor_rules():
    validate_anchor(make_anchor(), view_boxes={1: [0, 0, 595, 842]})
    with pytest.raises(SchemaViolation):
        validate_anchor(make_anchor(heading_level=3))
    with pytest.raises(SchemaViolation):
        validate_anchor(make_anchor(content_type="heading", heading_level=0))
    with pytest.raises(SchemaViolation):
        validate_anchor(make_anchor(bbox=[10, 10, 5, 20]))
    seen = set()
    validate_anchor(make_anchor(), seen_ids=seen)
    with pytest.raises(SchemaViolation):
        validate_anchor(make_anchor(), seen_ids=seen)


def test_snapshot_and_quote_matching():
    block = make_block()
    snap = block_snapshot(block)
    assert set(snap) >= {"title", "content", "sources", "status", "deleted_at"}
    assert "id" not in snap and "revision" not in snap
    assert quote_matches("行列式的定义：设 A 为 n 阶方阵", "行列式的定义")
    assert not quote_matches("行列式的定义", "矩阵的迹")
