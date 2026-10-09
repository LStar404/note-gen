"""块间链接规则（5.4 第 2 条）：同 anchor 的块互链（仅本章）；跨章 links 仅人工创建。"""

from __future__ import annotations

from src.core.models import BLOCK_SNAPSHOT_FIELDS


def suggest_links(chapter_id: str, blocks: list[dict], anchors: list[dict]) -> dict[str, list[str]]:
    """轻量规则：同 anchor 的块互链（单向，A→B 不自动回链）。"""
    by_anchor: dict[str, list[str]] = {}
    for b in blocks:
        if b.get("deleted_at"):
            continue
        for src in b.get("sources", []):
            by_anchor.setdefault(src["anchor_id"], []).append(b["id"])
    out: dict[str, list[str]] = {}
    for aid, ids in by_anchor.items():
        uniq = sorted(set(ids))
        if len(uniq) < 2:
            continue
        for i, bid in enumerate(uniq):
            for other in uniq[i + 1:]:
                out.setdefault(bid, []).append(other)
    return {k: sorted(set(v)) for k, v in out.items()}
