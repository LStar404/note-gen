"""导出渲染公共规则（9.6）：徽标、标题分组、占位符重写、引文渲染时重写。"""

from __future__ import annotations

import re
from typing import Any, Iterable

from src.core.block_types import RELATION_CN, get_type

FIG_PLACEHOLDER = re.compile(r"^!\[(fig_[0-9]{8})\]$")
FIG_ANY = re.compile(r"!\[(fig_[0-9]{8})\]")
CITE_PATTERN = re.compile(r"\[(\d{1,4})\]")
MATH_REGION = re.compile(r"(\$\$.*?\$\$|\$.*?\$)", re.DOTALL)
_FENCE = chr(96) * 3
CODE_FENCE = re.compile(_FENCE + ".*?" + _FENCE, re.DOTALL)


def block_order_key(block: dict) -> tuple:
    """不变量 I9：order 升序、同 order 按 block_id 字典序。"""
    return (float(block.get("order", 0.0)), str(block.get("id", "")))


def live_blocks(blocks: Iterable[dict]) -> list[dict]:
    return sorted([b for b in blocks if not b.get("deleted_at")], key=block_order_key)


def source_badges(block: dict, anchors_by_id: dict[str, dict], *, mode: str = "md") -> str:
    parts = []
    for src in block.get("sources", []):
        anchor = anchors_by_id.get(src["anchor_id"], {})
        page = anchor.get("page", "?")
        label = f"📖p.{page} · {RELATION_CN.get(src.get('relation'), src.get('relation', ''))}"
        if mode == "md":
            parts.append(f"[{label}](note://anchor/{src['anchor_id']}?page={page})")
        else:
            parts.append(f"（{label}）")
    if block.get("basis") == "synthesis":
        parts.append("（AI 归纳）" if mode != "md" else "（AI 归纳）")
    return " ".join(parts)


def heading_line(anchor: dict | None) -> str | None:
    """小节标题由块的 heading_anchor_id 驱动（9.6 规则 2）。"""
    if not anchor:
        return None
    level = int(anchor.get("heading_level") or 1)
    marks = "#" * min(3, max(1, level))
    return f"{marks} {anchor.get('content', '').strip()}"


def rewrite_figures(content: str, *, doc_id: str, mode: str, figure_paths: dict[str, str],
                    figure_captions: dict[str, str] | None = None) -> str:
    """图占位符重写：Viewer 用 API URL，导出用磁盘相对路径（7.4 两条规则不得混用）。"""
    figure_captions = figure_captions or {}

    def repl(m: re.Match) -> str:
        fig_id = m.group(1)
        caption = figure_captions.get(fig_id, figure_paths.get(fig_id, fig_id))
        if mode == "viewer":
            url = f"/api/v1/docs/{doc_id}/figures/{fig_id}"
            return f"![{caption}]({url})"
        return f"![{caption}]({figure_paths.get(fig_id, fig_id)})"

    return FIG_ANY.sub(repl, content)


def rewrite_citations(content: str, refs_by_num: dict[int, dict], *, mode: str) -> str:
    """引文标记渲染时重写（D18）：仅非数学区、非代码区。"""
    if not refs_by_num:
        return content
    protected: list[str] = []

    def stash(m: re.Match) -> str:
        protected.append(m.group(0))
        return f"\x00{len(protected) - 1}\x00"

    text = CODE_FENCE.sub(stash, content)
    text = MATH_REGION.sub(stash, text)

    def repl(m: re.Match) -> str:
        num = int(m.group(1))
        ref = refs_by_num.get(num)
        if not ref:
            return m.group(0)
        if mode == "md":
            return f"[{num}](note://ref/{ref['ref_id']})"
        return f"[{num}]({ref.get('url') or '#'})"

    text = CITE_PATTERN.sub(repl, text)
    for i, raw in enumerate(protected):
        text = text.replace(f"\x00{i}\x00", raw)
    return text


def figure_index(doc_id: str, store) -> tuple[dict[str, str], dict[str, str]]:
    paths: dict[str, str] = {}
    captions: dict[str, str] = {}
    for row in store.figure_rows():
        paths[row["figure_id"]] = row.get("path", "")
        if row.get("caption"):
            captions[row["figure_id"]] = row["caption"]
    return paths, captions
