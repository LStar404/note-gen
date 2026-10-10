"""再生成合并策略（5.7，架构红线）：人工成果永不丢失。

身份键 = (type, 排序后的 sources.anchor_id 集合)；无 sources 用 (type, normalize_text(title))。
generation_protected 永不被自动覆盖；只有完整提纲明确排除且无歧义时才软删旧 AI 块。
"""

from __future__ import annotations

from typing import Any

from src.core.text import new_block_id
from src.core.store import DocMutation, DocStore, sort_blocks
from src.core.text import normalize_text, utc_now_iso

GENERATION_FIELDS = ("title", "content", "content_format", "sources", "links", "order",
                     "heading_anchor_id", "type", "tags", "status", "basis")


def identity_key(block: dict) -> tuple:
    sources = sorted({s["anchor_id"] for s in block.get("sources", []) if s.get("anchor_id")})
    if sources:
        return (block.get("type"), tuple(sources))
    return (block.get("type"), normalize_text(block.get("title", "")))


def _deep_equal(a: dict, b: dict) -> bool:
    for field in GENERATION_FIELDS:
        av, bv = a.get(field), b.get(field)
        if field == "sources":
            av = [{k: s.get(k) for k in ("anchor_id", "quote", "relation")} for s in (av or [])]
            bv = [{k: s.get(k) for k in ("anchor_id", "quote", "relation")} for s in (bv or [])]
        if field in ("links", "tags"):
            av, bv = list(av or []), list(bv or [])
        if field == "order":
            av, bv = float(av or 0.0), float(bv or 0.0)
        if av != bv:
            return False
    return True


def merge_candidates(store: DocStore, chapter_id: str, candidates: list[dict], *,
                     keep_identities: set[tuple] | None = None, mode: str = "merge",
                     outline_complete: bool = True, now: str | None = None) -> tuple[DocMutation, dict]:
    now = now or utc_now_iso()
    report: dict[str, Any] = {
        "skipped_protected": [], "skipped_identical": [], "suppressed_deleted": [],
        "ambiguous": [], "orphan_kept": [], "created": [], "updated": [], "deleted": [],
        "restored": [], "failures": [],
    }
    existing = store.block_rows(chapter_id, include_deleted=True)
    live = [b for b in existing if not b.get("deleted_at")]
    tombs = [b for b in existing if b.get("deleted_at")]

    cand_keys = [identity_key(c) for c in candidates]
    key_counts: dict[tuple, int] = {}
    for k in cand_keys:
        key_counts[k] = key_counts.get(k, 0) + 1

    # ---- 一对一匹配 ----
    matched: dict[str, tuple[dict, dict]] = {}
    consumed: set[str] = set()
    for cand, key in zip(candidates, cand_keys):
        pool = [b for b in live if identity_key(b) == key and b["id"] not in consumed]
        if not pool:
            continue
        if len(pool) > 1 or key_counts[key] > 1:
            same_title = [b for b in pool if normalize_text(b.get("title", "")) == normalize_text(cand.get("title", ""))]
            if len(same_title) == 1:
                pool = same_title
            else:
                report["ambiguous"].append({"temp_id": cand.get("temp_id"), "key": str(key),
                                            "block_ids": [b["id"] for b in pool]})
                continue
        best = pool[0]
        consumed.add(best["id"])
        matched[cand.get("temp_id", "")] = (cand, best)

    new_rows: dict[str, dict] = {}
    history: list[dict] = []
    temp_to_id: dict[str, str] = {}
    keep_ids = set()

    for cand in candidates:
        tid = cand.get("temp_id", "")
        fill_failed = cand.get("_status") == "failed"
        if fill_failed:
            report["failures"].append({"temp_id": tid, "reason": "; ".join(cand.get("_errors", []))})
            if tid in matched:
                keep_ids.add(matched[tid][1]["id"])
            continue

        fields = {k: cand.get(k) for k in GENERATION_FIELDS if k in cand}
        if tid in matched:
            cand_obj, old = matched[tid]
            if old.get("generation_protected"):
                report["skipped_protected"].append(old["id"])
                keep_ids.add(old["id"])
                temp_to_id[tid] = old["id"]
                continue
            updated = dict(old)
            updated.update(fields)
            updated.setdefault("sources", cand.get("sources", []))
            updated["heading_anchor_id"] = cand.get("heading_anchor_id")
            updated["order"] = float(cand.get("order", old.get("order", 0.0)))
            if _deep_equal(old, updated):
                report["skipped_identical"].append(old["id"])
                keep_ids.add(old["id"])
                temp_to_id[tid] = old["id"]
                continue
            updated["revision"] = int(old["revision"]) + 1
            updated["updated_at"] = now
            updated["generation_protected"] = False
            new_rows[updated["id"]] = updated
            history.append(store.make_history_row(updated, "ai", "再生成替换", now))
            report["updated"].append(updated["id"])
            temp_to_id[tid] = updated["id"]
            continue

        # 未匹配：查墓碑
        key = identity_key(cand)
        tomb = next((b for b in tombs if identity_key(b) == key and b["id"] not in {r["id"] for r in new_rows.values()}), None)
        if tomb is not None:
            if tomb.get("generation_protected"):
                report["suppressed_deleted"].append(tomb["id"])
                continue
            block = dict(tomb)
            block.update(fields)
            block["deleted_at"] = None
            block["revision"] = int(tomb["revision"]) + 1
            block["updated_at"] = now
            new_rows[block["id"]] = block
            history.append(store.make_history_row(block, "ai", "生成器恢复未保护墓碑", now))
            report["restored"].append(block["id"])
            temp_to_id[tid] = block["id"]
            continue

        block = dict(cand)
        block.pop("_status", None)
        block.pop("_errors", None)
        block.pop("_warnings", None)
        block.pop("temp_id", None)
        block.pop("depends_on", None)
        block.pop("_heading_anchor_id", None)
        used = {b["id"] for b in existing} | set(new_rows)
        block["id"] = cand.get("_block_id") or new_block_id(used)
        block["doc_id"] = store.doc_id
        block["chapter_id"] = chapter_id
        block["created_by"] = cand.get("_created_by", "ai")
        block["revision"] = 1
        block["created_at"] = now
        block["updated_at"] = now
        block["deleted_at"] = None
        block["generation_protected"] = cand.get("_generation_protected", False)
        block.setdefault("links", [])
        block.setdefault("tags", [])
        block.setdefault("sources", [])
        block.setdefault("content_format", "md_latex")
        block.setdefault("basis", "anchored")
        block.setdefault("status", "ai_generated")
        new_rows[block["id"]] = block
        history.append(store.make_history_row(block, block["created_by"], "生成新建", now))
        report["created"].append(block["id"])
        temp_to_id[tid] = block["id"]

    # ---- 淘汰：仅完整提纲明确排除且无歧义时软删未保护旧 AI 块（5.7 第 4 条）----
    if mode == "merge" and outline_complete and not report["ambiguous"] and not report["failures"]:
        candidate_keys = set(cand_keys)
        for b in live:
            if b["id"] in keep_ids or b["id"] in new_rows:
                continue
            if b.get("generation_protected"):
                report["orphan_kept"].append(b["id"])
                continue
            if identity_key(b) in candidate_keys:
                continue
            removed = dict(b)
            removed["deleted_at"] = now
            removed["revision"] = int(b["revision"]) + 1
            removed["updated_at"] = now
            new_rows[removed["id"]] = removed
            history.append(store.make_history_row(removed, "ai", "再生成淘汰", now))
            report["deleted"].append(removed["id"])
    else:
        for b in live:
            if b["id"] not in new_rows and b["id"] not in keep_ids:
                if b.get("generation_protected"):
                    report["orphan_kept"].append(b["id"])

    # ---- links：temp_id → 实际 block_id（5.7 第 5 条）----
    for cand in candidates:
        tid = cand.get("temp_id", "")
        if tid not in temp_to_id:
            continue
        row = new_rows.get(temp_to_id[tid])
        if row is None:
            continue
        links = []
        for dep in cand.get("depends_on", []):
            target = temp_to_id.get(dep)
            if target and target != row["id"]:
                links.append(target)
        row["links"] = links

    merged = {**{b["id"]: b for b in existing if b["id"] not in new_rows}, **new_rows}
    rows = sort_blocks(merged.values())
    mutation = DocMutation(blocks={chapter_id: rows}, history=history)
    report["temp_to_id"] = temp_to_id
    return mutation, report
