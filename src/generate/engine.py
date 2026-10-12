"""生成编排（3.3 契约 generate_chapter）：outline → fill → 校验 → 5.7 合并 → 提交 → link。"""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
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


# 进程内按 (workspace, doc) 串行「读基线→merge→commit→link」（4.2.2 单写者）。
# 整本生成时各章 outline/fill 并行跑以提速，但写入必须排队：两章同时提交会让
# 后到者拿旧 data_revision 撞 RevisionConflict，白烧一章的 LLM 调用。
# 用户并发改动仍由 CAS 兜底：基线在锁内读取，锁外的用户提交要么先落（被 merge
# 读到）、要么后落（撞 CAS 由用户重试），不会互相吞掉。
_COMMIT_LOCKS: dict[str, threading.Lock] = {}
_COMMIT_LOCKS_GUARD = threading.Lock()


def _commit_lock(layout: Layout, doc_id: str) -> threading.Lock:
    key = f"{layout.root}|{doc_id}"
    with _COMMIT_LOCKS_GUARD:
        lock = _COMMIT_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _COMMIT_LOCKS[key] = lock
        return lock


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


def _build_candidates(items: list[dict], filled: dict[str, dict]) -> list[dict]:
    """提纲条目 + 填充结果 → 候选块（正常路径与断点快照共用同一构建）。"""
    out: list[dict] = []
    for item in items:
        f = filled.get(item["temp_id"], {})
        cand = dict(item)
        cand["sources"] = _normalize_sources(f.get("sources_patch", item.get("sources", [])))
        cand["basis"] = f.get("basis", item.get("basis", "anchored"))
        cand["content"] = f.get("content", "")
        cand["content_format"] = f.get("content_format", "md_latex")
        cand["tags"] = f.get("tags", [])
        cand["_status"] = f.get("_status", "ok") if f else "failed"
        cand["_errors"] = f.get("_errors", [])
        cand["status"] = "needs_review" if cand["_status"] == "needs_review" else "ai_generated"
        out.append(cand)
    return out


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
                "outline_max_out": config.get("generate", {}).get("outline_max_out", 16000),
                "fill_max_out": config.get("generate", {}).get("fill_max_out", 6000),
                "prompt_version": PROMPT_VERSION})

    report: dict[str, Any] = {
        "doc_id": doc_id, "chapter_id": chapter_id, "status": "complete",
        "cache_hit": False, "outline_complete": True, "created_at": utc_now_iso(),
        "model": {"outline": outline_model, "fill": fill_model},
        "failures": [],
    }
    t_start = time.monotonic()

    def _finish(rep: dict) -> dict:
        """收尾报告：总耗时 + LLM 调用遥测（排队长=全局闸紧，调用长=模型慢）。"""
        timing = rep.setdefault("timing", {})
        timing["total_s"] = round(time.monotonic() - t_start, 2)
        stats = getattr(client, "stats", None)
        if isinstance(stats, dict):
            timing["llm"] = {k: (round(v, 2) if isinstance(v, float) else v)
                             for k, v in stats.items()}
        _write_report(layout, doc_id, chapter_id, rep)
        return rep
    if progress:
        progress({"stage": "start", "chapter_id": chapter_id})

    cached = None
    if use_cache and not force:
        cached = read_cache(layout, doc_id, chapter_id, key)

    if cached is not None:
        report["cache_hit"] = True
        candidates = cached.get("candidates", [])
        report["outline_complete"] = cached.get("outline_complete", True)
        # 缓存里的失败候选必须重试：否则重跑同一章只是复读旧失败（一个块失败即永久失败）。
        # 部分失败的章节尤其需要：缓存带着 22 个失败项时，用户点「生成本章」期望的是补齐而不是原样重报。
        failed = [c for c in candidates if c.get("_status") == "failed" and c.get("temp_id")]
        if failed and client is not None:
            if progress:
                progress({"stage": "fill_retry", "count": len(failed)})
            retry_specs = [{"temp_id": c["temp_id"], "type": c.get("type"),
                            "title": c.get("title", ""), "basis": c.get("basis", "anchored"),
                            "sources": c.get("sources", []),
                            "depends_on": c.get("depends_on", [])} for c in failed]
            refilled = fill_all(client, doc_id=doc_id, chapter_id=chapter_id,
                                outline_items=retry_specs, anchors_by_id=anchors_by_id,
                                anchors=anchors, model=fill_model,
                                config={**config, "_profile": profile},
                                concurrency=int(config.get("generate", {}).get("concurrency", 2)),
                                budget=None, task_id=task_id, progress=progress,
                                is_cancelled=is_cancelled)
            for c in candidates:
                f = refilled.get(c["temp_id"])
                if not f or f.get("_status") == "failed":
                    continue
                c["sources"] = _normalize_sources(f.get("sources_patch", c.get("sources", [])))
                c["basis"] = f.get("basis", c.get("basis", "anchored"))
                c["content"] = f.get("content", "")
                c["content_format"] = f.get("content_format", "md_latex")
                c["tags"] = f.get("tags", [])
                c["_status"] = f.get("_status", "ok")
                c["_errors"] = f.get("_errors", [])
                c["status"] = "needs_review" if f.get("_status") == "needs_review" else "ai_generated"
            if use_cache:
                write_cache(layout, doc_id, chapter_id, key,
                            {"candidates": candidates,
                             "outline_complete": report["outline_complete"],
                             "meta": {"prompt_version": PROMPT_VERSION}})
    else:
        if client is None:
            raise PipelineError("未配置 LLM 客户端，且无可用缓存")
        budget = BudgetGuard(float(config.get("generate", {}).get("max_cost_per_chapter", 0.0) or 0.0))
        _check_cancel()
        fill_conc = int(config.get("generate", {}).get("concurrency", 2))
        # 提纲/填充流水线（提速「任务启动」）：每批提纲校验通过即开始填充该批，
        # 不等整章提纲完成——首条笔记的等待从「全部提纲 + 首次填充」缩到
        # 「首批提纲 + 首次填充」。concurrency=1 语义是严格串行，不做重叠；
        # depends_on 受校验约束只能指向同批 temp_id，逐批填充不破坏 5.3.1。
        pipelined = fill_conc > 1
        pipe_lock = threading.Lock()
        filled_all: dict[str, dict] = {}
        acc_items: list[dict] = []      # 已出批的提纲条目（断点快照用）
        fill_state = {"total": 0}
        batch_done: dict[int, int] = {}
        fill_futs: list = []
        batch_seq = [0]
        fill_pool = (ThreadPoolExecutor(max_workers=max(1, min(4, fill_conc)))
                     if pipelined else None)

        def _fill_progress(idx: int):
            def _p(evt: dict) -> None:
                if (evt or {}).get("stage") != "fill":
                    return
                with pipe_lock:
                    batch_done[idx] = int(evt.get("done") or 0)
                    done = sum(batch_done.values())
                    total = fill_state["total"]
                if progress:
                    progress({"stage": "fill", "done": done, "total": total})
            return _p

        def _run_fill(items: list[dict], idx: int) -> None:
            out = fill_all(client, doc_id=doc_id, chapter_id=chapter_id,
                           outline_items=items, anchors_by_id=anchors_by_id,
                           anchors=anchors, model=fill_model,
                           config={**config, "_profile": profile},
                           concurrency=fill_conc, budget=budget, task_id=task_id,
                           progress=_fill_progress(idx), is_cancelled=is_cancelled)
            with pipe_lock:
                filled_all.update(out)

        def _on_items(items: list[dict]) -> None:
            with pipe_lock:
                idx = batch_seq[0]
                batch_seq[0] += 1
                acc_items.extend(items)
                fill_state["total"] += len(items)
                done = sum(batch_done.values())
                total = fill_state["total"]
            if progress:
                progress({"stage": "fill", "done": done, "total": total})
            fill_futs.append(fill_pool.submit(_run_fill, items, idx))

        t_gen = time.monotonic()
        try:
            outline = generate_outline(client, doc_id=doc_id, chapter_id=chapter_id, manifest=manifest,
                                       anchors=anchors, model=outline_model,
                                       config={**config, "_profile": profile},
                                       budget=budget, task_id=task_id, progress=progress,
                                       is_cancelled=is_cancelled,
                                       on_items=_on_items if pipelined else None)
            report["outline_complete"] = outline.complete
            report["outline_errors"] = outline.errors
            if not outline.items:
                if fill_pool is not None:
                    fill_pool.shutdown(wait=True)
                report["status"] = "failed"
                report["failures"].append({"stage": "outline", "reason": "; ".join(outline.errors) or "空提纲"})
                _finish(report)
                return report
            if progress:
                progress({"stage": "outline_done", "count": len(outline.items)})
            _check_cancel()
            if pipelined:
                for fut in fill_futs:
                    fut.result()      # 填充内意外异常在此冒泡（LLM 失败已按块隔离）
                filled = filled_all
            else:
                filled = fill_all(client, doc_id=doc_id, chapter_id=chapter_id,
                                  outline_items=outline.items, anchors_by_id=anchors_by_id,
                                  anchors=anchors, model=fill_model,
                                  config={**config, "_profile": profile},
                                  concurrency=fill_conc, budget=budget, task_id=task_id,
                                  progress=progress, is_cancelled=is_cancelled)
        except BaseException:
            if fill_pool is not None:
                # 取消/异常：不等在途填充（它们会在下一个检查点自行退出）
                fill_pool.shutdown(wait=False, cancel_futures=True)
            # 断点保护：已产出结果落缓存（取消/配额/意外异常都不丢），
            # 下次生成自动续填失败块、复用已完成块——不再整章作废重烧
            try:
                with pipe_lock:
                    snap_items, snap_filled = list(acc_items), dict(filled_all)
                if use_cache and (snap_items or snap_filled):
                    write_cache(layout, doc_id, chapter_id, key,
                                {"candidates": _build_candidates(snap_items, snap_filled),
                                 "outline_complete": False,
                                 "meta": {"prompt_version": PROMPT_VERSION}})
            except Exception:
                pass      # 快照失败不得掩盖原始异常
            raise
        else:
            if fill_pool is not None:
                fill_pool.shutdown(wait=True)
        report.setdefault("timing", {})["gen_s"] = round(time.monotonic() - t_gen, 2)  # 提纲+填充墙钟（流水线重叠）
        candidates = _build_candidates(outline.items, filled)
        if use_cache:
            write_cache(layout, doc_id, chapter_id, key,
                        {"candidates": candidates, "outline_complete": outline.complete,
                         "meta": {"prompt_version": PROMPT_VERSION}})

    # 4.6.1 归一（缓存路径同样适用：旧缓存可能带着未归一的重复来源）
    for c in candidates:
        c["sources"] = _normalize_sources(c.get("sources"))

    candidates = _derive_heading_and_order(candidates, anchors, chapter_id)
    # 写入段整体按文档串行（_commit_lock）：并行分章时两章的 merge/commit/link
    # 排队执行，避免互撞 RevisionConflict。CAS 基线取在 merge 读取时刻（I7/11.6.3）：
    # outline+fill 可达十几分钟，若基线取在生成开始，期间用户随手编辑一块就让
    # 整章提交 Conflict、几十次 LLM 调用全部作废；merge 本就是按当前状态设计的
    # （保护块跳过），用 merge 时刻的 revision 既不丢用户并发改动、又不误伤在途生成。
    t_commit = time.monotonic()
    with _commit_lock(layout, doc_id):
        base_revision = store.data_revision()
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
            _finish(report)
            raise

        # 链接阶段（5.4）：本章块出链与反向索引全量校验（同锁内：link 自带 CAS 提交）
        try:
            from src.link.backlink import link_doc

            link_report = link_doc(doc_id, chapter_id=chapter_id, layout=layout)
            report["link"] = link_report
        except Exception as exc:  # 链接失败不影响已提交块
            report["link"] = {"error": str(exc)}

    report.setdefault("timing", {})["commit_s"] = round(time.monotonic() - t_commit, 2)  # 含锁排队+合并+提交+链接
    if report.get("failures") or merge_report.get("failures"):
        report["status"] = "partial"
    # 配额耗尽 = 「暂停」而非报错：已完成块已保存并会被下次生成复用
    if report.get("status") == "partial" and getattr(client, "halted_reason", None):
        report["status"] = "paused"
        report["resume_hint"] = (("配额已用尽（%s）：已完成的块已保存并在下次生成时复用；"
                                  "为供应商充值/换 Key/换模型后再次点击生成，将自动续填剩余块。")
                                 % str(client.halted_reason)[:120])
    _finish(report)
    return report


def _write_report(layout: Layout, doc_id: str, chapter_id: str, report: dict) -> None:
    out = layout.reports_doc_dir(doc_id) / f"{chapter_id}.generate.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
