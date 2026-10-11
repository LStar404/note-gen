"""阶段 4 Link（5.4）：反向索引全量重建 + 块间链接 + 校验 + 阶段指纹。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from src.core.jsonl import canonical_json
from src.core.paths import Layout
from src.core.store import DocMutation, DocStore
from src.core.text import utc_now_iso

from .block_links import suggest_links


def link_doc(doc_id: str, chapter_id: str | None = None, *, layout: Layout | None = None) -> dict:
    """默认全书；传 chapter_id 时仅重建该章块的出链与涉及其 sources 的反向索引。"""
    layout = layout or Layout(Path.cwd())
    store = DocStore(layout, doc_id)
    manifest = store.manifest()
    base_revision = store.data_revision()   # 乐观锁基线=读取时刻，防丢并发改动
    chapters = [chapter_id] if chapter_id else store.chapter_ids()

    anchors_by_ch = {ch: store.anchor_rows(ch) for ch in store.chapter_ids()}
    all_anchor_ids = {a["anchor_id"] for rows in anchors_by_ch.values() for a in rows}
    blocks_by_ch = {ch: store.block_rows(ch, include_deleted=True) for ch in store.chapter_ids()}
    all_blocks = [b for rows in blocks_by_ch.values() for b in rows]

    report: dict = {"doc_id": doc_id, "chapters": chapters, "dead_links": [],
                    "cross_chapter_warnings": [], "updated_blocks": [], "created_at": utc_now_iso()}

    # ---- 反向索引（I3）：全量重算，先清空候选再填入，按 chapter_id/order/block_id 稳定排序 ----
    backlinks = store.compute_backlinks(all_blocks)
    anchor_updates: dict[str, list[dict]] = {}
    for ch, rows in anchors_by_ch.items():
        changed = False
        new_rows = []
        for a in rows:
            want = backlinks.get(a["anchor_id"], [])
            if list(a.get("note_block_ids", [])) != want:
                a = dict(a)
                a["note_block_ids"] = want
                changed = True
            new_rows.append(a)
        if changed:
            anchor_updates[ch] = new_rows

    # ---- 块间链接 ----
    history_rows = []
    block_updates: dict[str, list[dict]] = {}
    now = utc_now_iso()
    for ch in chapters:
        rows = [dict(b) for b in blocks_by_ch.get(ch, [])]
        suggestions = suggest_links(ch, rows, anchors_by_ch.get(ch, []))
        changed = False
        for b in rows:
            if b.get("deleted_at"):
                continue
            if b.get("generation_protected"):
                continue  # 保护块的 links 不被自动重写
            want = list(b.get("links", []))
            for target in suggestions.get(b["id"], []):
                if target not in want:
                    want.append(target)
            # 校验 links 目标
            valid = []
            for ln in want:
                if ln.startswith("http://") or ln.startswith("https://") or ln.startswith("ref_"):
                    valid.append(ln)
                    continue
                if ln not in all_anchor_ids and ln in {x["id"] for x in all_blocks if x.get("deleted_at")}:
                    report["dead_links"].append({"block_id": b["id"], "link": ln, "reason": "deleted_target"})
                    continue
                target = next((x for x in all_blocks if x["id"] == ln), None)
                if target is None:
                    report["dead_links"].append({"block_id": b["id"], "link": ln, "reason": "missing_target"})
                    continue
                if chapter_id and target["chapter_id"] != ch:
                    report["cross_chapter_warnings"].append({"block_id": b["id"], "link": ln})
                valid.append(ln)
            if valid != b.get("links", []):
                b["links"] = valid
                b["revision"] = int(b["revision"]) + 1
                b["updated_at"] = now
                history_rows.append(store.make_history_row(b, "ai", "link 阶段更新链接", now))
                report["updated_blocks"].append(b["id"])
                changed = True
        if changed:
            block_updates[ch] = rows

    committed_rev = base_revision
    if block_updates or anchor_updates:
        mutation = DocMutation(blocks=block_updates, anchors=anchor_updates, history=history_rows)
        committed_rev = store.commit(mutation, base_revision=base_revision, sync_backlinks=True)
    else:
        # 无业务变更：不改 revision/历史/时间（5.6 幂等）
        report["skipped"] = True

    # ---- 阶段指纹（对提交后的 sources/links/backlink 计算，排除 revision/时间）----
    blocks_after = store.all_block_rows(include_deleted=True)
    anchors_after = store.all_anchor_rows()
    fingerprint = hashlib.sha256(canonical_json({
        "sources": [(b["id"], b.get("sources")) for b in sorted(blocks_after, key=lambda x: x["id"])],
        "links": [(b["id"], b.get("links")) for b in sorted(blocks_after, key=lambda x: x["id"])],
        "backlinks": sorted((a["anchor_id"], a.get("note_block_ids")) for a in anchors_after),
    }).encode("utf-8")).hexdigest()
    stage_hashes = dict(manifest.get("stage_hashes", {}))
    stage_hashes["link"] = {"input_hash": "sha256:" + fingerprint, "output_hash": "sha256:" + fingerprint,
                            "version": "1"}
    if manifest.get("stage_hashes", {}).get("link", {}).get("output_hash") != stage_hashes["link"]["output_hash"]:
        # 续在上一提交的新 revision 上，两次提交都受乐观锁保护
        store.commit(DocMutation(manifest_updates={"stage_hashes": stage_hashes}),
                     base_revision=committed_rev, sync_backlinks=False)
    report["stage_hash"] = stage_hashes["link"]["output_hash"]
    _write_report(layout, doc_id, report)
    return report


def _write_report(layout: Layout, doc_id: str, report: dict) -> None:
    out = layout.reports_doc_dir(doc_id) / "link.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
