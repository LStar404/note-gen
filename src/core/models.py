"""4.x 全部实体 Pydantic 模型 + 4.7 校验器（字段名即规范，不得改名）。"""

from __future__ import annotations

import math
import re
from typing import Any, Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field

from .block_types import REGISTRY, SOURCE_RELATIONS, get_type, validate_type_constraints
from .coord import validate_bbox
from .errors import AnchorNotFound, SchemaViolation
from .paths import (
    BLOCK_ID_RE,
    CHAPTER_ID_RE,
    DOC_ID_RE,
    FIGURE_ID_RE,
    ANCHOR_ID_RE,
    REF_ID_RE,
    REV_ID_RE,
)
from .text import normalize_text

SCHEMA_VERSION = 2

ContentFormat = Literal["md_latex", "tex", "plain"]
BlockStatus = Literal["ai_generated", "hybrid", "manually_edited", "needs_review"]
Basis = Literal["anchored", "synthesis"]
CreatedBy = Literal["ai", "human", "agent"]
ContentType = Literal["paragraph", "heading", "formula", "figure", "table", "ref_entry"]
ParseStatus = Literal["ok", "blank", "failed"]
Profile = Literal["textbook", "paper"]

BLOCK_STATUSES = ("ai_generated", "hybrid", "manually_edited", "needs_review")
CONTENT_FORMATS = ("md_latex", "tex", "plain")
BASES = ("anchored", "synthesis")
CREATED_BY = ("ai", "human", "agent")
CONTENT_TYPES = ("paragraph", "heading", "formula", "figure", "table", "ref_entry")
PROFILES = ("textbook", "paper")

ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
MAX_CONTENT_BYTES = 50 * 1024  # 4.7：≤ 50 KiB（按字节计）
MAX_QUOTE_LEN = 300
MAX_TITLE_LEN = 80

# 块可变字段（历史快照字段，4.4.6）
BLOCK_SNAPSHOT_FIELDS = (
    "title", "content", "content_format", "sources", "links", "order",
    "heading_anchor_id", "type", "tags", "status", "basis", "deleted_at",
    "generation_protected",
)


class _Base(BaseModel):
    # 读改写必须保留未知字段（4.2）
    model_config = ConfigDict(extra="allow")


class PageMeta(_Base):
    page: int
    view_box: list[float]
    user_unit: float = 1.0
    rotation: int = 0
    parse_status: ParseStatus = "ok"


class ParserInfo(_Base):
    name: str
    version: str
    config_hash: str


class Chapter(_Base):
    chapter_id: str
    title: str
    page_start: int
    page_end: int
    outline_hash: str | None = None
    part: str | None = None


class StageFingerprint(_Base):
    input_hash: str
    output_hash: str
    version: str


class Manifest(_Base):
    schema_version: int = SCHEMA_VERSION
    doc_id: str
    profile: Profile = "textbook"
    title: str
    pdf_path: str
    pdf_sha256: str
    parser: ParserInfo
    ocr: bool = False
    data_revision: int = 1
    pages: list[PageMeta] = Field(default_factory=list)
    collision_map: dict[str, Any] = Field(default_factory=dict)
    chapters: list[Chapter] = Field(default_factory=list)
    stage_hashes: dict[str, StageFingerprint] = Field(default_factory=dict)
    created_at: str
    updated_at: str


class SourceRef(_Base):
    anchor_id: str
    quote: str
    relation: Literal["defines", "states", "proves", "motivates", "illustrates", "summarizes"]


class ParagraphAnchor(_Base):
    anchor_id: str
    doc_id: str
    chapter_id: str
    page: int
    reading_order: int
    bbox: list[float]
    text_hash: str
    content: str
    content_type: ContentType
    heading_level: int = 0
    embedding: list[float] | None = None
    continuation_of: str | None = None
    note_block_ids: list[str] = Field(default_factory=list)


class NoteBlock(_Base):
    id: str
    doc_id: str
    chapter_id: str
    type: str
    title: str
    content: str
    content_format: ContentFormat = "md_latex"
    order: float
    heading_anchor_id: str | None = None
    sources: list[SourceRef] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list)
    status: BlockStatus = "ai_generated"
    basis: Basis = "anchored"
    tags: list[str] = Field(default_factory=list)
    created_by: CreatedBy = "ai"
    generation_protected: bool = False
    revision: int = 1
    created_at: str
    updated_at: str
    deleted_at: str | None = None


class Figure(_Base):
    figure_id: str
    doc_id: str
    page: int
    bbox: list[float]
    path: str
    caption: str | None = None
    width_pt: float
    height_pt: float
    created_at: str


class BlockRevision(_Base):
    rev_id: str
    block_id: str
    snapshot: dict[str, Any]
    author: Literal["human", "ai", "agent"]
    message: str
    created_at: str


class Reference(_Base):
    ref_id: str
    doc_id: str
    num: int
    raw: str
    title: str | None = None
    authors: list[str] | None = None
    year: int | None = None
    url: str | None = None
    anchor_id: str
    anchor_ids: list[str] | None = None


# ---------------------------------------------------------------------------
# 校验器（4.7，写入前强制调用）
# ---------------------------------------------------------------------------

def check_iso_time(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not ISO_RE.match(value):
        raise SchemaViolation(f"{field_name} 必须是 UTC ISO-8601（YYYY-MM-DDTHH:MM:SSZ）: {value!r}",
                              code="TIME_INVALID")


def validate_anchor(anchor: ParagraphAnchor, *, view_boxes: dict[int, list[float]] | None = None,
                    seen_ids: set[str] | None = None) -> None:
    if not ANCHOR_ID_RE.match(anchor.anchor_id):
        raise SchemaViolation(f"anchor_id 不符合 4.2.1: {anchor.anchor_id!r}")
    if not DOC_ID_RE.match(anchor.doc_id):
        raise SchemaViolation(f"doc_id 非法: {anchor.doc_id!r}")
    if not CHAPTER_ID_RE.match(anchor.chapter_id):
        raise SchemaViolation(f"chapter_id 非法: {anchor.chapter_id!r}")
    if anchor.content_type not in CONTENT_TYPES:
        raise SchemaViolation(f"content_type 非法: {anchor.content_type!r}")
    if anchor.content_type == "heading":
        if not (1 <= int(anchor.heading_level) <= 6):
            raise SchemaViolation(f"heading_level 必须 1–6: {anchor.heading_level!r}")
    elif int(anchor.heading_level) != 0:
        raise SchemaViolation(f"非 heading 锚点 heading_level 必须为 0: {anchor.heading_level!r}")
    if not isinstance(anchor.reading_order, int) or anchor.reading_order < 0:
        raise SchemaViolation(f"reading_order 必须为页内非负整数: {anchor.reading_order!r}")
    vb = (view_boxes or {}).get(anchor.page)
    if vb is not None:
        validate_bbox(anchor.bbox, vb)
    else:
        validate_bbox(anchor.bbox, [anchor.bbox[0] - 1e6, anchor.bbox[1] - 1e6,
                                    anchor.bbox[2] + 1e6, anchor.bbox[3] + 1e6])
    if not isinstance(anchor.text_hash, str) or len(anchor.text_hash) != 64:
        raise SchemaViolation("text_hash 必须为 64 位 hex")
    if anchor.continuation_of is not None and not ANCHOR_ID_RE.match(anchor.continuation_of):
        raise SchemaViolation(f"continuation_of 非法: {anchor.continuation_of!r}")
    if seen_ids is not None:
        if anchor.anchor_id in seen_ids:
            raise SchemaViolation(f"anchor_id 重复: {anchor.anchor_id}")
        seen_ids.add(anchor.anchor_id)


def validate_block(
    block: NoteBlock,
    *,
    anchor_ids: Iterable[str] | None = None,
    heading_ids: Iterable[str] | None = None,
    block_ids: Iterable[str] | None = None,
    profile: str = "textbook",
) -> None:
    if not BLOCK_ID_RE.match(block.id):
        raise SchemaViolation(f"block_id 不符合 4.2.1: {block.id!r}")
    if not DOC_ID_RE.match(block.doc_id):
        raise SchemaViolation(f"doc_id 非法: {block.doc_id!r}")
    if not CHAPTER_ID_RE.match(block.chapter_id):
        raise SchemaViolation(f"chapter_id 非法: {block.chapter_id!r}")
    get_type(block.type)  # 未知类型抛错
    if block.type not in REGISTRY or profile not in REGISTRY[block.type].profiles:
        raise SchemaViolation(f"类型 {block.type} 不属于 profile={profile}")
    if not isinstance(block.title, str) or not block.title.strip():
        raise SchemaViolation("title 不能为空")
    if len(block.title) > MAX_TITLE_LEN:
        raise SchemaViolation(f"title 超过 {MAX_TITLE_LEN} 字符")
    if not isinstance(block.content, str):
        raise SchemaViolation("content 必须为字符串")
    nbytes = len(block.content.encode("utf-8"))
    if nbytes > MAX_CONTENT_BYTES:
        raise SchemaViolation(f"content 超过 50 KiB（{nbytes} 字节）", code="CONTENT_TOO_LARGE")
    if block.content_format not in CONTENT_FORMATS:
        raise SchemaViolation(f"content_format 非法: {block.content_format!r}")
    if not isinstance(block.order, float) and not isinstance(block.order, int):
        raise SchemaViolation(f"order 必须为数值: {block.order!r}")
    if not math.isfinite(float(block.order)):
        raise SchemaViolation("order 不能是 NaN/Infinity")
    if block.status not in BLOCK_STATUSES:
        raise SchemaViolation(f"status 非法: {block.status!r}")
    if block.basis not in BASES:
        raise SchemaViolation(f"basis 非法: {block.basis!r}")
    if block.created_by not in CREATED_BY:
        raise SchemaViolation(f"created_by 非法: {block.created_by!r}")
    if not isinstance(block.revision, int) or block.revision < 1:
        raise SchemaViolation(f"revision 必须 >=1: {block.revision!r}")
    check_iso_time(block.created_at, "created_at")
    check_iso_time(block.updated_at, "updated_at")
    if block.deleted_at is not None:
        check_iso_time(block.deleted_at, "deleted_at")

    # sources（4.6.1）
    if len(block.sources) > 5:
        raise SchemaViolation("sources 数量不能超过 5")
    seen_anchor: set[str] = set()
    for src in block.sources:
        if src.relation not in SOURCE_RELATIONS:
            raise SchemaViolation(f"relation 非法: {src.relation!r}")
        if not isinstance(src.quote, str) or len(src.quote) > MAX_QUOTE_LEN:
            raise SchemaViolation("quote 必须为不超过 300 字符的字符串")
        if src.anchor_id in seen_anchor:
            raise SchemaViolation(f"同一 anchor_id 在一块内重复: {src.anchor_id}")
        seen_anchor.add(src.anchor_id)
        if anchor_ids is not None and src.anchor_id not in anchor_ids:
            raise AnchorNotFound(f"来源锚点不存在: {src.anchor_id}")
    if block.heading_anchor_id is not None:
        if heading_ids is not None and block.heading_anchor_id not in heading_ids:
            raise SchemaViolation(f"heading_anchor_id 不是本 doc 同章 heading 锚点: {block.heading_anchor_id}")
    # 类型来源约束（4.4.4）
    validate_type_constraints(block.type, basis=block.basis, source_count=len(block.sources))
    # links（4.6.2）
    for ln in block.links:
        if not isinstance(ln, str) or not ln:
            raise SchemaViolation(f"links 元素非法: {ln!r}")
        if ln.startswith("http://") or ln.startswith("https://") or ln.startswith("ref_"):
            continue
        if not BLOCK_ID_RE.match(ln):
            raise SchemaViolation(f"links 元素既非块 id 也非 URL/ref_: {ln!r}")


def validate_reference(ref: Reference) -> None:
    if not REF_ID_RE.match(ref.ref_id):
        raise SchemaViolation(f"ref_id 非法: {ref.ref_id!r}")
    if not ANCHOR_ID_RE.match(ref.anchor_id):
        raise SchemaViolation(f"Reference.anchor_id 非法: {ref.anchor_id!r}")
    if not isinstance(ref.num, int) or ref.num < 1:
        raise SchemaViolation(f"Reference.num 非法: {ref.num!r}")
    if not isinstance(ref.raw, str) or not ref.raw.strip():
        raise SchemaViolation("Reference.raw 不能为空")


def block_snapshot(block: NoteBlock | dict) -> dict:
    """历史快照 = 该版本可变字段的完整快照（4.4.6）。"""
    data = block if isinstance(block, dict) else block.model_dump()
    return {k: data.get(k) for k in BLOCK_SNAPSHOT_FIELDS}


def quote_matches(anchor_content: str, quote: str) -> bool:
    """quote 必须是 normalize_text(anchor.content) 的连续子串（4.6.1）。"""
    return normalize_text(quote) in normalize_text(anchor_content) and bool(normalize_text(quote))
