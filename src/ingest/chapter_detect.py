"""章节检测（5.1）：手动覆盖 > 书签/目录 > 标题正则启发式；paper profile 固定单章。

页区间按 PDF 页序；此时不写 outline_hash（由 anchor 阶段写入，P1-3）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

CH_HEADING_RE = re.compile(
    r"^\s*(第[一二三四五六七八九十百零\d]+[章讲部编篇]|Chapter\s+\d+|Lecture\s+\d+|Part\s+[IVXLC\d]+)",
    re.IGNORECASE)
# 纯数字章号（LaTeX 常见：章号与章题分成两个版面块，如「3」「群作用」）
CH_BARE_NUM_RE = re.compile(r"^\d{1,2}$")
_CH_NUM_FROM_TEXT = re.compile(r"(\d+)")


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

    # 长前言/引言补一个章节（否则开篇 5+ 页没有归属，无法生成笔记）；
    # 只在缺口足够大时补，避免给「第 1 章从第 4 页开始」的书平白加章
    if starts[0][1] > 5:
        starts.insert(0, (_front_matter_title(pages_dir) or "前言", 1))

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


_LEADER_RE = re.compile(r"(\.\s*){5,}")      # 目录引导线：连续 5+ 个点


def _is_toc_page(blocks: list[dict]) -> bool:
    """目录页识别：带引导线（. . . . .）的行占比高即跳过——
    目录行会伪装成「纯数字章号 + 短标题」，是误检的最大来源。
    注意不能用「出现多个点/省略号」的宽判：数学正文里的 \u00b7 · · · 省略号
    会把普通正文页误杀（实测于品《群论》第 19 页）。"""
    if not blocks:
        return False
    dotted = sum(1 for b in blocks if _LEADER_RE.search(b.get("text") or ""))
    return dotted >= 3 and dotted / len(blocks) >= 0.15


def _longest_seq_chain(cands: list[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
    """在（页码, 章号, 标题）候选里找最长的 +1 递增链：章号必须连号（1,2,3…/0,1,2…/
    任意起始），这是滤掉页码、列表项等裸数字误检的最强约束。"""
    if not cands:
        return []
    ordered = sorted(cands, key=lambda x: (x[0], x[1]))
    best: list[tuple[int, int, str]] = []
    for i, start in enumerate(ordered):
        chain = [start]
        want = start[1] + 1
        for c in ordered[i + 1:]:
            if c[0] > chain[-1][0] and c[1] == want:
                chain.append(c)
                want += 1
        if len(chain) > len(best):
            best = chain
    return best


def _front_matter_title(pages_dir: Path) -> str | None:
    """前言/引言标题探测：开篇章题常见「引言/引论/绪论/前言/Introduction」。"""
    if not pages_dir or not Path(pages_dir).exists():
        return None
    pat = re.compile(r"^(引言|引论|绪论|前言|导言|Introduction|Preliminaries)", re.IGNORECASE)
    for page_file in sorted(Path(pages_dir).glob("*.json"))[:8]:
        try:
            data = json.loads(page_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        for blk in data.get("blocks", []):
            t = (blk.get("text") or "").strip()
            if blk.get("type") == "heading" and pat.match(t):
                return t[:20]
    return None


def _title_like(text: str) -> bool:
    """章题必须是文字（含中文或 ≥2 个字母），而非公式/符号碎片（如 '<' 'ξ−1'）。"""
    t = text.strip()
    if not t or len(t) > 40:
        return False
    has_cjk = any("\u4e00" <= c <= "\u9fff" for c in t)
    letters = sum(1 for c in t if c.isascii() and c.isalpha())
    mathy = sum(1 for c in t if c in "+-*/^_()[]{}<>|=,.;:!?" or ord(c) in (0x2212, 0x2208, 0x2218))
    return has_cjk or (letters >= 2 and mathy <= max(1, len(t) // 6))


def _match_key(text: str):
    """同一章号在 running header 里会逐页重复——按章号（无数字按标题）去重，取首次出现。"""
    m = _CH_NUM_FROM_TEXT.search(text)
    return ("n", int(m.group(1))) if m else ("t", re.sub(r"\s+", "", text)[:24])


def _from_heading_pages(pages_dir: Path) -> list[tuple[str, int]]:
    """启发式兜底：一级标题扫描（5.1）。

    覆盖两类正文形态：
    1. 显式章题：第X章/讲/部/编/篇、Chapter N、Lecture N、Part N（整块即标题）；
    2. 纯数字章号（LaTeX 书常见，如于品《群论》：「3」与「群作用」分两个块）：
       要求章号块为 heading 类型（或紧邻块为 heading/短标题），再用「连号链」
       过滤目录页码/列表项误检。目录页整页跳过。
    """
    starts: list[tuple[str, int]] = []
    bare: list[tuple[int, int, str]] = []          # (page, num, title)
    if not pages_dir or not Path(pages_dir).exists():
        return []
    for page_file in sorted(Path(pages_dir).glob("*.json")):
        try:
            data = json.loads(page_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        page = int(data.get("page", 0))
        blocks = data.get("blocks", [])
        if _is_toc_page(blocks):
            continue
        texts = [(b.get("text") or "").strip() for b in blocks]
        height = float(data.get("page_height") or 0) or 1.0
        for idx, blk in enumerate(blocks):
            text = texts[idx]
            if not text:
                continue
            if CH_HEADING_RE.match(text):
                starts.append((text[:80], page))
                continue
            if not CH_BARE_NUM_RE.fullmatch(text) or blk.get("type") != "heading":
                continue
            # 章号块必须是大字号标题且在页面顶部（正文里的公式编号/列表项都是 paragraph）
            y1 = float((blk.get("bbox") or [0, 0, 0, 0])[3] or 0)
            if height and y1 < height * 0.55:
                continue
            num = int(text)
            if num > 30:
                continue
            # 标题 = 紧随其后的文本块：优先 heading 型，且必须是"文字"而非公式碎片
            tail = blocks[idx + 1: idx + 4]
            title = ""
            for tb in tail:
                t = (tb.get("text") or "").strip()
                if tb.get("type") == "heading" and _title_like(t):
                    title = t
                    break
            if not title:
                for tb in tail:
                    t = (tb.get("text") or "").strip()
                    if _title_like(t):
                        title = t
                        break
            title = re.sub(r"[.\s\u2026]+$", "", title)[:60]
            bare.append((page, num, f"{num} {title}".strip()))

    if bare:
        chain = _longest_seq_chain(bare)
        if len(chain) >= 2:
            starts.extend((title, page) for page, _num, title in chain)

    starts.sort(key=lambda t: t[1])
    seen: set = set()
    out = []
    for title, page in starts:
        key = _match_key(title)
        if key in seen or page in seen:
            continue
        seen.add(key)
        seen.add(page)
        out.append((title, page))
    return out
