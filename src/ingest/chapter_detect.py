"""章节检测（5.1）：手动覆盖 > 书签/目录 > 标题正则启发式；paper profile 固定单章。

页区间按 PDF 页序；此时不写 outline_hash（由 anchor 阶段写入，P1-3）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

CH_HEADING_RE = re.compile(r"(第[一二三四五六七八九十百零\d]+章|Chapter\s+\d+)", re.IGNORECASE)


def _chapter_id(index: int) -> str:
    return f"ch{index:02d}"


def detect_chapters(reader, page_records: list[dict], doc_id: str, *, pages_dir: Path,
                    profile: str = "textbook", override: list | None = None) -> list[dict]:
    n_pages = len(page_records)
    if profile == "paper":
        title = _outline_title(reader) or "论文"
        return [{"chapter_id": "ch01", "title": title, "page_start": 1, "page_end": n_pages}]

    if override:
        chapters = []
        for i, item in enumerate(override, start=1):
            chapters.append({
                "chapter_id": item.get("chapter_id") or _chapter_id(i),
                "title": item.get("title") or f"第 {i} 章",
                "page_start": int(item["page_start"]),
                "page_end": int(item["page_end"]),
            })
        return _finish(chapters, n_pages)

    starts = _from_outline(reader)
    if not starts:
        starts = _from_heading_pages(pages_dir)
    if not starts:
        return [{"chapter_id": "ch01", "title": _outline_title(reader) or "全书",
                 "page_start": 1, "page_end": n_pages}]

    chapters = []
    for i, (title, page) in enumerate(starts):
        page_end = (starts[i + 1][1] - 1) if i + 1 < len(starts) else n_pages
        chapters.append({"chapter_id": _chapter_id(i + 1), "title": title,
                         "page_start": int(page), "page_end": int(page_end)})
    return _finish(chapters, n_pages)


def _finish(chapters: list[dict], n_pages: int) -> list[dict]:
    out = []
    for c in chapters:
        c = dict(c)
        c["page_start"] = max(1, int(c["page_start"]))
        c["page_end"] = min(n_pages, max(1, int(c["page_end"])))
        c.setdefault("outline_hash", None)
        out.append(c)
    return out


def _outline_title(reader) -> str | None:
    try:
        for item in reader.outline:
            if isinstance(item, list):
                continue
            return getattr(item, "title", None) or str(item)
    except Exception:
        return None
    return None


def _from_outline(reader) -> list[tuple[str, int]]:
    """目录优先：解析 PDF 书签/目录页，取各章标题与起始页（深度 0 为章）。"""
    starts: list[tuple[str, int]] = []

    def walk(items, depth=0):
        for item in items:
            if isinstance(item, list):
                walk(item, depth + 1)
                continue
            title = getattr(item, "title", None) or str(item)
            try:
                page = reader.get_destination_page_number(item) + 1
            except Exception:
                continue
            if depth == 0 and title.strip():
                starts.append((title.strip(), page))

    try:
        walk(reader.outline or [])
    except Exception:
        return []
    starts.sort(key=lambda t: t[1])
    return starts


def _from_heading_pages(pages_dir: Path) -> list[tuple[str, int]]:
    """启发式兜底：正则匹配一级标题（第X章 / Chapter N）在版面块中的出现位置。"""
    starts: list[tuple[str, int]] = []
    if not pages_dir or not Path(pages_dir).exists():
        return []
    for page_file in sorted(Path(pages_dir).glob("*.json")):
        try:
            data = json.loads(page_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        page = int(data.get("page", 0))
        for blk in data.get("blocks", []):
            text = (blk.get("text") or "").strip()
            if CH_HEADING_RE.search(text):
                starts.append((text, page))
    starts.sort(key=lambda t: t[1])
    return starts
