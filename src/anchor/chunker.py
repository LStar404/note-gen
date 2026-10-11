"""阶段 2 Anchor：版面块 → 段落级锚点（5.2）。

切分规则、保守归一化、内容哈希派生 ID 与全文档重复消歧、reading_order、跨页 continuation_of
均在此实现；重锚定先暂存，配对迁移成功后一次提交（5.5）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from src.core.errors import PipelineError, SchemaViolation
from src.core.jsonl import canonical_json
from src.core.models import ParagraphAnchor, Reference, validate_anchor
from src.core.paths import Layout
from src.core.store import DocMutation, DocStore
from src.core.text import base_anchor_id, text_hash, utc_now_iso, with_suffix

SENTENCE_END = "。！？.!?；;"
MAX_PARA_CHARS = 800

REF_ITEM_RE = re.compile(r"^\s*(?:\[(\d+)\]|(\d+)[\.\)])\s+")
BLOCK_START_RE = re.compile(
    r"^(definition|theorem|lemma|proposition|corollary|proof|motivation|example|remark|figure|table)\b",
    re.IGNORECASE)
PAPER_HEADING_RE = re.compile(r"^\d+(\.\d+)*\.?\s+[A-Z][\w\s\-]{2,}$")
PAPER_SECTION_NAMES = {
    "abstract", "introduction", "related work", "background", "method", "methodology",
    "approach", "experiment", "experiments", "evaluation", "results", "discussion",
    "conclusion", "references", "appendix",
}


class _Item:
    __slots__ = ("content", "content_type", "heading_level", "page", "bbox",
                 "reading_order", "continuation_of", "figure_id", "_prev")

    def __init__(self, content, content_type, page, bbox, heading_level=0,
                 reading_order=0, continuation_of=None, figure_id=None):
        self.content = content
        self.content_type = content_type
        self.heading_level = heading_level
        self.page = page
        self.bbox = bbox
        self.reading_order = reading_order
        self.continuation_of = continuation_of
        self.figure_id = figure_id


def chunk_and_anchor(doc_id: str, chapter_id: str, *, layout: Layout | None = None,
                     force: bool = False) -> int:
    """版面块 → 锚点，返回锚点数；写 anchors/{chapter_id}.jsonl 与 manifest 指纹。"""
    layout = layout or Layout(Path.cwd())
    store = DocStore(layout, doc_id)
    manifest = store.manifest()
    base_revision = store.data_revision()   # 乐观锁基线=读取时刻，防丢并发改动
    chapter = store.chapter(chapter_id)
    pages_dir = layout.ingest_pages(doc_id)
    if not pages_dir.exists():
        raise PipelineError(f"缺少解析产物: {pages_dir}，请先运行 ingest")

    items: list[_Item] = []
    collision_map: dict[str, list[dict]] = dict(manifest.get("collision_map", {}))

    for page in range(int(chapter["page_start"]), int(chapter["page_end"]) + 1):
        page_file = pages_dir / f"{page:04d}.json"
        if not page_file.exists():
            continue
        data = json.loads(page_file.read_text(encoding="utf-8"))
        items.extend(_split_page(data))

    _assign_reading_order(items)
    _link_continuations(items)
    rows = _materialize(doc_id, chapter_id, items, layout=layout, store=store,
                        collision_map=collision_map)

    # paper profile：References 区条目（5.8.1/5.8.2）
    refs: list[dict] = []
    if manifest.get("profile") == "paper":
        rows, refs = _extract_references(doc_id, rows)

    # 重锚定保护：sources 迁移配对（5.5）；迁移结果与新锚点同一次提交
    block_updates, history_rows = _migrate_sources(store, doc_id, chapter_id, rows)

    outline_hash = "sha256:" + _outline_hash(rows)
    manifest_updates = {
        "collision_map": collision_map,
        "chapters": [
            ({**c, "outline_hash": outline_hash} if c["chapter_id"] == chapter_id else c)
            for c in manifest.get("chapters", [])
        ],
        "stage_hashes": {
            **manifest.get("stage_hashes", {}),
            "anchor": {
                "input_hash": "sha256:" + _input_hash(layout, doc_id, chapter),
                "output_hash": "sha256:" + _output_hash(rows),
                "version": "2",
            },
        },
    }
    mutation = DocMutation(anchors={chapter_id: rows}, refs=refs if refs else None,
                           blocks=block_updates, history=history_rows,
                           manifest_updates=manifest_updates)
    store.commit(mutation, base_revision=base_revision, sync_backlinks=True)
    return len(rows)


# ---------------------------------------------------------------------------
# 切分（5.2 MUST）
# ---------------------------------------------------------------------------
def _split_page(page_data: dict) -> list[_Item]:
    page = int(page_data["page"])
    blocks = page_data.get("blocks", [])
    items: list[_Item] = []
    buf: list[dict] = []

    def flush():
        nonlocal buf
        if not buf:
            return
        text = "".join(b["text"] for b in buf).strip()
        if text:
            bbox = [min(b["bbox"][0] for b in buf), min(b["bbox"][1] for b in buf),
                    max(b["bbox"][2] for b in buf), max(b["bbox"][3] for b in buf)]
            for piece in _split_long(text, bbox):
                items.append(_Item(piece[0], "paragraph", page, piece[1]))
        buf = []

    for blk in blocks:
        btype = blk.get("type", "paragraph")
        text = (blk.get("text") or "").strip()
        if not text:
            continue
        if btype == "heading":
            flush()
            level = int(blk.get("level") or 2)
            items.append(_Item(text, "heading", page, list(blk["bbox"]),
                               heading_level=min(6, max(1, level))))
        elif btype in ("formula", "figure", "table"):
            flush()
            content = text
            if btype == "figure":
                content = f"![{blk.get('figure_id') or 'fig_unknown'}]"
            items.append(_Item(content, btype, page, list(blk["bbox"])))
        else:
            prev_text = (buf[-1].get("text") or "").rstrip() if buf else ""
            prev_done = bool(prev_text) and prev_text[-1] in SENTENCE_END
            if buf and (BLOCK_START_RE.match(text) or prev_done or
                        sum(len(b["text"]) for b in buf) + len(text) > MAX_PARA_CHARS):
                flush()   # 标记句/句末/超限：另起锚点
            buf.append(blk)
    flush()
    return items


def _split_long(text: str, bbox: list[float]) -> list[tuple[str, list[float]]]:
    """合并上限 800 字符；超限按句末标点/换行切分。"""
    if len(text) <= MAX_PARA_CHARS:
        return [(text, bbox)]
    pieces: list[str] = []
    cur = ""
    for ch in text:
        cur += ch
        if ch in SENTENCE_END and len(cur) >= MAX_PARA_CHARS // 2:
            pieces.append(cur)
            cur = ""
    if cur.strip():
        pieces.append(cur)
    out = []
    n = len(pieces) or 1
    width = (bbox[2] - bbox[0]) / n
    for i, piece in enumerate(pieces):
        out.append((piece.strip(), [bbox[0] + i * width, bbox[1], bbox[0] + (i + 1) * width, bbox[3]]))
    return [(p, b) for p, b in out if p]


def _assign_reading_order(items: list[_Item]) -> None:
    """页内唯一非负递增阅读序；heading/公式切分/跨栏均不得被段落合并吞掉。"""
    per_page: dict[int, int] = {}
    for it in items:
        n = per_page.get(it.page, 0)
        it.reading_order = n
        per_page[it.page] = n + 1


def _link_continuations(items: list[_Item]) -> None:
    """跨页续段：上一锚尾部无句末标点 + 下一锚首字符小写/无句首标点（4.3.5）。"""
    by_page: dict[int, list[_Item]] = {}
    for it in items:
        by_page.setdefault(it.page, []).append(it)
    pages = sorted(by_page)
    for i in range(len(pages) - 1):
        cur, nxt = by_page[pages[i]], by_page[pages[i + 1]]
        if not cur or not nxt:
            continue
        last, first = cur[-1], nxt[0]
        if last.content_type != "paragraph" or first.content_type != "paragraph":
            continue
        tail = last.content.rstrip()
        head = first.content.lstrip()
        if not tail or tail[-1] in SENTENCE_END:
            continue
        if head and (head[0].islower() or head[0] not in "。！？.!?；;"):
            first._prev = last  # _materialize 里回填实际 id


# ---------------------------------------------------------------------------
# ID 分配与去重（5.2）
# ---------------------------------------------------------------------------
def _materialize(doc_id: str, chapter_id: str, items: list[_Item], *, layout: Layout,
                 store: DocStore, collision_map: dict) -> list[dict]:
    # 已有 ID 池（其它章节为永久占用；本章旧数据将被替换），保证全文档唯一
    taken: dict[str, str] = {}          # anchor_id -> text_hash（其它章节）
    old_by_key: dict[tuple, str] = {}
    for ch in store.chapter_ids():
        for row in store.anchor_rows(ch):
            if ch == chapter_id:
                old_by_key[(row.get("text_hash"), row.get("chapter_id"), row.get("page"),
                            row.get("content"))] = row["anchor_id"]
            else:
                taken[row["anchor_id"]] = row.get("text_hash", "")

    view_boxes = {p["page"]: p["view_box"] for p in store.manifest().get("pages", [])}

    # 先算基础 ID 与哈希，再按 (page, reading_order) 全局分配重复后缀
    prepared = []
    for it in items:
        th = text_hash(it.content)
        prepared.append((it, th, base_anchor_id(doc_id, it.content)))

    counts: dict[str, int] = {}
    rows: list[dict] = []
    assigned: dict[str, str] = {}
    for it, th, base in prepared:
        # 重解析尽力复用旧实例 ID（5.5）：同内容 + 同章 + 同页位次
        reuse = old_by_key.get((th, chapter_id, it.page, it.content))
        if reuse and reuse not in {r["anchor_id"] for r in rows}:
            anchor_id = reuse
        else:
            # 全文档重复消歧（5.2）：同基础 ID 第一个用基础 ID，后续 -2/-3/…
            n = counts.get(base, 0)
            while True:
                n += 1
                cand = with_suffix(base, n)
                holder = taken.get(cand)
                if holder is None:
                    break
                if holder != th:
                    # 同截断哈希但内容不同：真正的哈希碰撞，不得覆盖
                    raise SchemaViolation(f"anchor_id 冲突（截断哈希碰撞）: {cand}",
                                          code="ANCHOR_COLLISION")
            counts[base] = n
            anchor_id = cand
        taken[anchor_id] = th

        if anchor_id != base:
            collision_map.setdefault(base, [])
        row = {
            "anchor_id": anchor_id,
            "doc_id": doc_id,
            "chapter_id": chapter_id,
            "page": int(it.page),
            "reading_order": int(it.reading_order),
            "bbox": [float(v) for v in it.bbox],
            "text_hash": th,
            "content": it.content,
            "content_type": it.content_type,
            "heading_level": int(it.heading_level),
            "embedding": None,
            "continuation_of": None,
            "note_block_ids": [],
        }
        validate_anchor(ParagraphAnchor.model_validate(row), view_boxes=view_boxes)
        rows.append(row)
        assigned[(id(it))] = anchor_id

    # 回填 continuation_of
    for it, th, base in prepared:
        prev = getattr(it, "_prev", None)
        if prev is not None:
            cur_id = assigned[id(it)]
            prev_id = assigned.get(id(prev))
            for row in rows:
                if row["anchor_id"] == cur_id and prev_id:
                    row["continuation_of"] = prev_id

    # 记录碰撞映射（5.2）：基础 ID → 实例列表
    by_base: dict[str, list[dict]] = {}
    for (it, th, base), row in zip(prepared, rows):
        if counts.get(base, 0) > 1 or row["anchor_id"] != base:
            by_base.setdefault(base, []).append({
                "anchor_id": row["anchor_id"], "text_hash": th, "page": row["page"],
                "reading_order": row["reading_order"], "chapter_id": chapter_id,
                "prev_hash": None, "next_hash": None,
            })
    collision_map.update(by_base)
    return rows


def _outline_hash(rows: list[dict]) -> str:
    """稳定输入指纹（5.3.4）：排除 note_block_ids/embedding；键排序紧凑编码。"""
    import hashlib

    projection = [
        {k: r.get(k) for k in ("anchor_id", "page", "bbox", "reading_order", "content",
                               "content_type", "heading_level", "continuation_of")}
        for r in rows
    ]
    return hashlib.sha256(canonical_json(projection).encode("utf-8")).hexdigest()


def _input_hash(layout: Layout, doc_id: str, chapter: dict) -> str:
    import hashlib

    pages_dir = layout.ingest_pages(doc_id)
    h = hashlib.sha256()
    for page in range(int(chapter["page_start"]), int(chapter["page_end"]) + 1):
        p = pages_dir / f"{page:04d}.json"
        if p.exists():
            h.update(p.name.encode("utf-8"))
            h.update(p.read_bytes())
    return h.hexdigest()


def _output_hash(rows: list[dict]) -> str:
    import hashlib

    return hashlib.sha256(canonical_json(rows).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# paper：References 条目（5.8.1 / 5.8.2，确定性优先）
# ---------------------------------------------------------------------------
def _extract_references(doc_id: str, rows: list[dict]) -> tuple[list[dict], list[dict]]:
    ref_start = None
    for i, row in enumerate(rows):
        if row["content_type"] == "heading" and row["content"].strip().lower() in ("references", "参考文献", "bibliography"):
            ref_start = i
            break
    if ref_start is None:
        return rows, []

    new_rows = rows[:ref_start + 1]
    ref_rows: list[dict] = []
    seq = 0
    for row in rows[ref_start + 1:]:
        if row["content_type"] == "heading":
            new_rows.append(row)
            continue
        item = dict(row)
        item["content_type"] = "ref_entry"
        seq += 1
        m = REF_ITEM_RE.match(item["content"])
        if m:
            num = int(m.group(1) or m.group(2))
        else:
            num = None
        ref_rows.append({"row": item, "num": num})
        new_rows.append(item)

    refs: list[dict] = []
    seen_nums: set[int] = set()
    unsupported: list[str] = []
    idx = 0
    for entry in ref_rows:
        item = entry["row"]
        num = entry["num"]
        if num is None or num in seen_nums:
            unsupported.append(item["content"][:80])
            continue
        seen_nums.add(num)
        idx += 1
        ref = {
            "ref_id": f"ref_{idx:04d}",
            "doc_id": doc_id,
            "num": num,
            "raw": item["content"],
            "title": None,
            "authors": None,
            "year": None,
            "url": None,
            "anchor_id": item["anchor_id"],
            "anchor_ids": [item["anchor_id"]],
        }
        Reference.model_validate(ref)
        refs.append(ref)
    return new_rows, refs


# ---------------------------------------------------------------------------
# 来源迁移（5.5）：重锚定后配对 sources，失败则保留旧快照
# ---------------------------------------------------------------------------
def _migrate_sources(store: DocStore, doc_id: str, chapter_id: str,
                     new_rows: list[dict]) -> tuple[dict[str, list[dict]], list[dict]]:
    """配对 sources/heading/continuation；返回需要重写的块行与历史行（5.5）。"""
    new_ids = {r["anchor_id"] for r in new_rows}
    new_by_hash: dict[str, list[str]] = {}
    for r in new_rows:
        new_by_hash.setdefault(r["text_hash"], []).append(r["anchor_id"])

    old_rows = store.anchor_rows(chapter_id) if chapter_id in store.chapter_ids() else []
    old_by_id = {r["anchor_id"]: r for r in old_rows}
    remap: dict[str, str] = {}
    unresolved: list[dict] = []

    for old_id, old in old_by_id.items():
        if old_id in new_ids:
            continue
        cands = new_by_hash.get(old.get("text_hash", ""), [])
        if len(cands) == 1:
            remap[old_id] = cands[0]
        elif len(cands) > 1:
            unresolved.append({"old": old_id, "reason": "duplicate_quote_ambiguous", "candidates": cands})
        else:
            unresolved.append({"old": old_id, "reason": "no_candidate"})

    # 检查全部块 sources 是否仍有效
    broken: list[dict] = []
    for b in store.all_block_rows(include_deleted=True):
        for src in b.get("sources", []):
            aid = src["anchor_id"]
            if aid in new_ids:
                continue
            if aid in remap:
                continue
            broken.append({"block_id": b["id"], "anchor_id": aid})
    if broken or unresolved:
        report = {
            "doc_id": doc_id, "chapter_id": chapter_id,
            "unresolved": unresolved, "broken_sources": broken,
            "created_at": utc_now_iso(),
        }
        out = layout_reports(store.layout, doc_id) / f"{chapter_id}.migration.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        raise PipelineError(
            f"重锚定后存在无法配对的来源，保留旧快照（见 {out}）",
            detail={"broken": len(broken), "unresolved": len(unresolved)})

    if not remap:
        return {}, []
    now = utc_now_iso()
    updates: dict[str, list[dict]] = {}
    history_rows: list[dict] = []
    for ch in store.chapter_ids():
        rows = [dict(b) for b in store.block_rows(ch, include_deleted=True)]
        touched = False
        for b in rows:
            changed = False
            for src in b.get("sources", []):
                if src["anchor_id"] in remap:
                    src["anchor_id"] = remap[src["anchor_id"]]
                    changed = True
            if b.get("heading_anchor_id") in remap:
                b["heading_anchor_id"] = remap[b["heading_anchor_id"]]
                changed = True
            if changed:
                touched = True
                b["revision"] = int(b["revision"]) + 1
                b["updated_at"] = now
                history_rows.append(store.make_history_row(b, "ai", "重锚定来源迁移", now))
        if touched:
            updates[ch] = rows
    return updates, history_rows


def layout_reports(layout: Layout, doc_id: str) -> Path:
    return layout.reports_doc_dir(doc_id)
