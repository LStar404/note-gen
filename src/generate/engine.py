"""生成编排（3.3 契约 generate_chapter）：outline → fill → 校验 → 5.7 合并 → 提交 → link。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from src.core.errors import PipelineError, RevisionConflict, TaskCancelled
from src.core.llm import BudgetGuard, LLMClient
from src.core.model_registry import ModelRegistry
from src.core.paths import Layout
from src.core.store import DocStore
from src.core.text import utc_now_iso

from .cache import cache_key, model_fingerprint, read_cache, write_cache
from .fill import fill_all
from .merge import merge_candidates
from .outline import generate_outline
from .prompts import PROMPT_VERSION


def _derive_heading_and_order(items: list[dict], anchors: list[dict], chapter_id: str) -> list[dict]:
    """生成后置处理（5.3.1）：heading_anchor_id 与 order 的确定性规则，不交给模型。"""
    ordered = sorted(anchors, key=lambda a: (a["page"], a["reading_order"]))
    index = {a["anchor_id"]: i for i, a in enumerate(ordered)}
    headings = [(i, a) for i, a in enumerate(ordered) if a.get("content_type") == "heading"]
    out = []
    for item in items:
        sources = item.get("sources", [])
        local = [s for s in sources if s["anchor_id"] in index]
        heading_id = None
        base_index = None
        if local:
            first = min(local, key=lambda s: index[s["anchor_id"]])
            base_index = index[first["anchor_id"]]
            anchor = ordered[base_index]
            if anchor.get("content_type") == "heading":
                heading_id = anchor["anchor_id"]
            else:
                prior = [h for h in headings if h[0] < base_index]
                heading_id = prior[-1][1]["anchor_id"] if prior else None
        elif item.get("depends_on"):
            deps = [d for d in item["depends_on"]]
            for dep in deps:
                src_item = next((x for x in items if x.get("temp_id") == dep), None)
                if src_item and src_item.get("_heading_anchor_id"):
                    heading_id = src_item["_heading_anchor_id"]
                    break
        item = dict(item)
        item["_heading_anchor_id"] = heading_id
        item["heading_anchor_id"] = heading_id
        if base_index is not None:
            order = (base_index + 1) * 10.0
        else:
            order = float(len(ordered) + 1) * 10.0
        if item.get("type") == "motivation":
            order = max(1.0, order - 5.0)
        if item.get("type") == "link":
            order = float(len(ordered) + 1) * 10.0
        item["order"] = float(order)
        out.append(item)
    return out


def _normalize_sources(sources: list | None) -> list:
    """4.6.1：一块内 anchor_id 唯一、≤5 条——模型输出归一，避免 commit 阶段才失败。"""
    out: list = []
    seen: set = set()
    for src in sources or []:
        aid = (src or {}).get("anchor_id")
        if aid is None or aid in seen:
            continue
        seen.add(aid)
        out.append(src)
    return out[:5]


def generate_chapter(doc_id: str, chapter_id: str, *, layout: Layout | None = None,
                     client: LLMClient | None = None, config: dict | None = None,
                     registry: ModelRegistry | None = None, model: str | None = None,
                     force: bool = False, use_cache: bool = True, merge_mode: str = "merge",
                     task_id: str | None = None,
                     progress: Callable[[dict], None] | None = None,
                     is_cancelled: Callable[[], bool] | None = None) -> dict:
    """分章生成笔记块；返回 ChapterReport（部分失败不误删旧块）。"""
    layout = layout or Layout(Path.cwd())
    config = config or {}
    store = DocStore(layout, doc_id)
    manifest = store.manifest()
    profile = manifest.get("profile", "textbook")
    chapter = store.chapter(chapter_id)
    anchors = store.anchor_rows(chapter_id)
    anchors_by_id = {a["anchor_id"]: a for a in anchors}
    if not anchors:
        raise PipelineError(f"章节 {chapter_id} 没有锚点，请先运行 anchor")

    def _check_cancel() -> None:
        if is_cancelled and is_cancelled():
            raise TaskCancelled("任务已取消")

    base_revision = store.data_revision()
    outline_hash = chapter.get("outline_hash") or ""
    registry = registry or ModelRegistry(layout)
    models_cfg = config.get("generate", {}).get("models", {}) or {}
    outline_model = model or models_cfg.get("outline") or models_cfg.get("default")
    fill_model = model or models_cfg.get("fill") or models_cfg.get("default")
    key = cache_key(
        doc_id=doc_id, chapter_id=chapter_id, outline_hash=outline_hash, profile=profile,
        registry_version="1",
        outline_model=model_fingerprint(registry, outline_model),
        fill_model=model_fingerprint(registry, fill_model),
        params={"temperature": config.get("generate", {}).get("temperature"),
                "prompt_version": PROMPT_VERSION})

    report: dict[str, Any] = {
        "doc_id": doc_id, "chapter_id": chapter_id, "status": "complete",
        "cache_hit": False, "outline_complete": True, "created_at": utc_now_iso(),
        "model": {"outline": outline_model, "fill": fill_model},
        "failures": [],
    }
    if progress:
        progress({"stage": "start", "chapter_id": chapter_id})

    cached = None
    if use_cache and not force:
        cached = read_cache(layout, doc_id, chapter_id, key)

    if cached is not None:
        report["cache_hit"] = True
        candidates = cached.get("candidates", [])
        report["outline_complete"] = cached.get("outline_complete", True)
    else:
        if client is None:
            raise PipelineError("未配置 LLM 客户端，且无可用缓存")
        budget = BudgetGuard(float(config.get("generate", {}).get("max_cost_per_chapter", 0.0) or 0.0))
        _check_cancel()
        outline = generate_outline(client, doc_id=doc_id, chapter_id=chapter_id, manifest=manifest,
                                   anchors=anchors, model=outline_model, config={**config, "_profile": profile},
                                   budget=budget, task_id=task_id, progress=progress,
                                   is_cancelled=is_cancelled)
        report["outline_complete"] = outline.complete
        report["outline_errors"] = outline.errors
        if not outline.items:
            report["status"] = "failed"
            report["failures"].append({"stage": "outline", "reason": "; ".join(outline.errors) or "空提纲"})
            _write_report(layout, doc_id, chapter_id, report)
            return report
        if progress:
            progress({"stage": "outline_done", "count": len(outline.items)})
        _check_cancel()
        filled = fill_all(client, doc_id=doc_id, chapter_id=chapter_id,
                          outline_items=outline.items, anchors_by_id=anchors_by_id,
                          anchors=anchors, model=fill_model,
                          config={**config, "_profile": profile},
                          concurrency=int(config.get("generate", {}).get("concurrency", 2)),
                          budget=budget, task_id=task_id, progress=progress,
                          is_cancelled=is_cancelled)
        candidates = []
        for item in outline.items:
            f = filled.get(item["temp_id"], {})
            cand = dict(item)
            cand["sources"] = _normalize_sources(f.get("sources_patch", item.get("sources", [])))
            cand["basis"] = f.get("basis", item.get("basis", "anchored"))
            cand["content"] = f.get("content", "")
            cand["content_format"] = f.get("content_format", "md_latex")
            cand["tags"] = f.get("tags", [])
            cand["_status"] = f.get("_status", "ok")
            cand["_errors"] = f.get("_errors", [])
            cand["status"] = "needs_review" if f.get("_status") == "needs_review" else "ai_generated"
            candidates.append(cand)
        if use_cache:
            write_cache(layout, doc_id, chapter_id, key,
                        {"candidates": candidates, "outline_complete": outline.complete,
                         "meta": {"prompt_version": PROMPT_VERSION}})

    candidates = _derive_heading_and_order(candidates, anchors, chapter_id)
    # 失败候选必须交给合并器：保留对应旧块并进报告，不能据此删除（5.7 第 1 条）
    mutation, merge_report = merge_candidates(store, chapter_id, candidates,
                                              mode=merge_mode,
                                              outline_complete=report["outline_complete"])
    report.update({k: v for k, v in merge_report.items() if k != "temp_to_id"})
    if merge_report.get("failures"):
        report["status"] = "partial"

    _check_cancel()
    try:
        store.commit(mutation, base_revision=base_revision, sync_backlinks=True)
    except RevisionConflict as exc:
        report["status"] = "failed"
        report["failures"].append({"stage": "commit", "reason": exc.message})
        _write_report(layout, doc_id, chapter_id, report)
        raise

    # 链接阶段（5.4）：本章块出链与反向索引全量校验
    try:
        from src.link.backlink import link_doc

        link_report = link_doc(doc_id, chapter_id=chapter_id, layout=layout)
        report["link"] = link_report
    except Exception as exc:  # 链接失败不影响已提交块
        report["link"] = {"error": str(exc)}

    if report.get("failures") or merge_report.get("failures"):
        report["status"] = "partial"
    _write_report(layout, doc_id, chapter_id, report)
    return report


def _write_report(layout: Layout, doc_id: str, chapter_id: str, report: dict) -> None:
    out = layout.reports_doc_dir(doc_id) / f"{chapter_id}.generate.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
