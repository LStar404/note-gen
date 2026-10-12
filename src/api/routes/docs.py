"""文档/PDF/图/页/选区/检索/生成触发端点（9.2）。"""

from __future__ import annotations

import json
import math
import threading
from pathlib import Path

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from src.core.errors import (ConflictError, InputError, NoteGenError,
                             NotFound, PipelineError, PriceUnknown,
                             TaskCancelled)
from src.core.paths import DOC_ID_RE, FIGURE_ID_RE
from src.core.store import DocMutation, DocStore
from src.db import queries
from src.ingest.parse_pdf import parse_pdf_with_fallback

router = APIRouter()


def state(request: Request):
    return request.app.state.app_state


@router.get("/api/v1/docs")
def list_docs(request: Request):
    st = state(request)
    out = []
    for path in sorted(st.layout.data_dir.glob("*/manifest.json")):
        if not DOC_ID_RE.match(path.parent.name):
            continue            # 跳过 .bak-* 等备份目录
        try:
            import json

            m = json.loads(path.read_text(encoding="utf-8"))
            out.append({"doc_id": m["doc_id"], "title": m.get("title"),
                        "profile": m.get("profile", "textbook"),
                        "chapters": m.get("chapters", []),
                        "data_revision": m.get("data_revision"),
                        "updated_at": m.get("updated_at")})
        except Exception:
            continue
    return {"docs": out}


@router.post("/api/v1/docs")
async def create_doc(request: Request, file: UploadFile = File(...), title: str | None = Form(None),
                     profile: str = Form("textbook"), doc_id: str | None = Form(None),
                     overwrite: bool = Form(False)):
    st = state(request)
    if not file.filename.lower().endswith(".pdf"):
        raise InputError("只支持 PDF 文件")
    if not (file.filename or "").strip():
        raise InputError("文件名为空")
    data = await file.read()
    if len(data) > 200 * 1024 * 1024:
        raise InputError("PDF 超过 200 MiB 限制")
    import hashlib

    doc = doc_id or ("doc-" + hashlib.sha256(data).hexdigest()[:12])
    if not DOC_ID_RE.match(doc):
        raise InputError(f"doc_id 非法: {doc}")
    # 重复导入（同哈希/同 doc_id）：默认拒绝，避免覆盖已有笔记；overwrite 显式确认后重建
    if st.layout.manifest(doc).exists():
        if not overwrite:
            raise ConflictError(
                f"文档已存在：{doc}（同名/同哈希 PDF 已导入）",
                detail={"doc_id": doc,
                        "hint": "如需重新导入，请先在 UI 删除该教材，或传 overwrite=true（将重建解析产物）"})
        import shutil

        shutil.rmtree(st.layout.doc_dir(doc), ignore_errors=True)
    st.layout.textbooks_dir.mkdir(parents=True, exist_ok=True)
    target = st.layout.textbooks_dir / f"{doc}.pdf"
    target.write_bytes(data)

    def run(req, progress, is_cancelled, register_artifact, task_id=None):
        progress({"stage": "ingest", "pages_done": 0, "pages_total": None})
        manifest = parse_pdf_with_fallback(target, st.layout.ingest_root,
                                           st.config.get("parser", {}).get("default", "auto"),
                                           layout=st.layout, doc_id=doc, profile=profile,
                                           title=title or Path(file.filename).stem,
                                           config=st.config, progress=progress)
        from src.anchor.chunker import chunk_and_anchor

        counts = {}
        chapters = manifest["chapters"]
        for idx, ch in enumerate(chapters):
            # 按章报锚定进度（9.2）：导入是两段式，进度条要能持续推进
            progress({"stage": "anchor", "done": idx, "total": len(chapters),
                      "chapter_id": ch["chapter_id"]})
            counts[ch["chapter_id"]] = chunk_and_anchor(doc, ch["chapter_id"], layout=st.layout)
        progress({"stage": "anchor", "done": len(chapters), "total": len(chapters)})
        return {"doc_id": doc, "chapters": manifest["chapters"], "anchors": counts}

    task = st.tasks.submit("import", run, {"doc_id": doc, "filename": file.filename},
                           doc_id=doc, idempotency_key=None)
    return JSONResponse({"task_id": task["task_id"], "doc_id": doc}, status_code=202)


@router.post("/api/v1/docs/{doc_id}/rebuild")
def rebuild_doc(request: Request, doc_id: str, body: dict | None = None):
    """重建导入（不重新上传 PDF）：重跑解析 + 分章 + 锚定，
    用于拾取新的解析器（如中文保真抽取）与新的分章规则。
    仅允许无笔记块的文档——已有块按旧 chapter_id 归属，重建会错位。"""
    st = state(request)
    store = DocStore(st.layout, doc_id)
    manifest = store.manifest()
    if store.all_block_rows(include_deleted=False):
        raise ConflictError(
            "文档已有笔记块，重建导入会使其与新章节错位；请先导出笔记或删除块后再试",
            code="HAS_BLOCKS")
    pdf = st.layout.root / manifest["pdf_path"]
    if not pdf.exists():
        raise NotFound("PDF 文件不存在，无法重建")
    title = manifest.get("title")
    profile = manifest.get("profile", "textbook")

    def run(req, progress, is_cancelled, register_artifact, task_id=None):
        import shutil

        from src.anchor.chunker import chunk_and_anchor
        from src.db.runtime import get_runtime

        # 旧解析/锚定/图资源整体作废（PDF 保留在 data/textbooks 不动）
        shutil.rmtree(st.layout.doc_dir(doc_id), ignore_errors=True)
        shutil.rmtree(st.layout.ingest_root / doc_id, ignore_errors=True)
        get_runtime(st.layout.runtime_db).forget_doc(doc_id)
        progress({"stage": "ingest", "pages_done": 0, "pages_total": None})
        manifest_new = parse_pdf_with_fallback(
            pdf, st.layout.ingest_root, st.config.get("parser", {}).get("default", "auto"),
            layout=st.layout, doc_id=doc_id, profile=profile, title=title,
            config=st.config, progress=progress)
        chapters = manifest_new["chapters"]
        counts = {}
        for idx, ch in enumerate(chapters):
            if is_cancelled():
                raise TaskCancelled("任务已取消")
            progress({"stage": "anchor", "done": idx, "total": len(chapters),
                      "chapter_id": ch["chapter_id"]})
            counts[ch["chapter_id"]] = chunk_and_anchor(doc_id, ch["chapter_id"], layout=st.layout)
        progress({"stage": "anchor", "done": len(chapters), "total": len(chapters)})
        return {"doc_id": doc_id, "chapters": chapters, "anchors": counts}

    task = st.tasks.submit("import", run, {"doc_id": doc_id, "mode": "rebuild"},
                           doc_id=doc_id)
    return JSONResponse({"task_id": task["task_id"], "doc_id": doc_id}, status_code=202)


@router.post("/api/v1/docs/{doc_id}/refresh-figures")
def refresh_figures_api(request: Request, doc_id: str, body: dict | None = None):
    """刷新图资源（不重新导入）：从 PDF 内嵌图登记 figures/ 与图锚点，
    并把既有笔记里悬空的 ![{fig}] 占位引用改指到真实图（图在源锚点同页且唯一时）。"""
    import re

    from src.anchor.chunker import chunk_and_anchor
    from src.core.text import utc_now_iso
    from src.ingest.figures import refresh_figures

    st = state(request)
    store = DocStore(st.layout, doc_id)
    manifest = store.manifest()
    pdf = st.layout.root / manifest["pdf_path"]
    figs = refresh_figures(pdf, st.layout, doc_id,
                           parser_python=(st.config.get("parser", {}) or {}).get("python"))

    # 重锚定（5.5 来源迁移）：pages JSON 补了 figure 块 → 生成图锚点，
    # 既有块的 sources 由迁移对配自动维护（锚点 id 是内容哈希，稳定）
    for ch in store.chapter_ids():
        chunk_and_anchor(doc_id, ch, layout=st.layout)

    # 既有笔记的悬空图片引用修复：源锚点同页恰有一张图 → 改指真实 id
    fig_dir = st.layout.figures_dir(doc_id)
    pages_dir = st.layout.ingest_root / doc_id / "pages"
    fig_by_page: dict[int, set[str]] = {}
    for pf in pages_dir.glob("*.json"):
        try:
            d = json.loads(pf.read_text(encoding="utf-8"))
        except Exception:
            continue
        for b in d.get("blocks", []):
            if b.get("figure_id"):
                fig_by_page.setdefault(int(d.get("page", 0)), []).append(
                    (b["figure_id"], b.get("bbox") or [0, 0, 0, 0]))
    ref_re = re.compile(r"!\[(fig_\d{8})\]")
    now = utc_now_iso()
    patched = 0

    def _vgap(a: list, f: list) -> float:
        """锚点与图的纵向间距（重叠为 0）：源文本通常紧贴所引插图。"""
        return max(0.0, float(f[1]) - float(a[3]), float(a[1]) - float(f[3]))
    for ch in store.chapter_ids():
        rows = [dict(b) for b in store.block_rows(ch, include_deleted=True)]
        anchors_by_id = {a["anchor_id"]: a for a in store.anchor_rows(ch)}
        history = []
        changed = False
        for b in rows:
            if b.get("deleted_at"):
                continue
            content = b.get("content") or ""
            refs = ref_re.findall(content)
            unresolved = [r for r in set(refs) if not (fig_dir / f"{r}.png").exists()]
            if not unresolved:
                continue
            src_anchors = [anchors_by_id.get(a.get("anchor_id"), {})
                           for a in (b.get("sources") or [])]
            primary_page = next((a.get("page") for a in src_anchors if a.get("page")), None)
            scored: list[tuple[float, int, str]] = []
            for pg in {a.get("page") for a in src_anchors}:
                if not pg:
                    continue
                same_page = [a for a in src_anchors if a.get("page") == pg and a.get("bbox")]
                for fid, fbbox in fig_by_page.get(int(pg), []):
                    gaps = [_vgap(a["bbox"], fbbox) for a in same_page]
                    if gaps:
                        # 只在同页锚点间比距离（跨页坐标不可比）；首选来源页破平
                        scored.append((min(gaps), 0 if pg == primary_page else 1, fid))
            scored.sort()
            # 与次近差距 <60pt（且同破平级）视为歧义，不猜（前端优雅降级）
            if not scored:
                continue
            if len(scored) > 1 and scored[1][:2] == scored[0][:2] and scored[1][0] - scored[0][0] < 60:
                continue
            real = scored[0][2]
            for r in unresolved:
                content = content.replace(f"![{r}]", f"![{real}]")
            if content != b["content"]:
                b["content"] = content
                b["revision"] = int(b["revision"]) + 1
                b["updated_at"] = now
                history.append(store.make_history_row(b, "ai", "图片引用修复", now))
                changed = True
                patched += 1
        if changed:
            store.commit(DocMutation(blocks={ch: rows}, history=history),
                         base_revision=store.data_revision(), sync_backlinks=True)
    return {"figures": len(figs), "patched_blocks": patched}


@router.get("/api/v1/docs/{doc_id}")
def get_doc(request: Request, doc_id: str):
    st = state(request)
    return DocStore(st.layout, doc_id).manifest()


@router.delete("/api/v1/docs/{doc_id}")
def delete_doc(request: Request, doc_id: str, body: dict | None = None):
    """删除教材（移入回收站，可恢复）。需 confirmed=true 显式确认（4.1 文档事实源）。"""
    body = body or {}
    if not body.get("confirmed"):
        raise InputError("删除教材需要 confirmed=true（将移入回收站，可在 data/.trash 找回）")
    st = state(request)
    store = DocStore(st.layout, doc_id)
    summary = {
        "blocks": len(store.all_block_rows(include_deleted=True)),
        "anchors": len(store.all_anchor_rows()),
        "chapters": len(store.chapter_ids()),
    }
    result = store.trash_document()
    result["removed"] = summary
    return result


@router.get("/api/v1/docs/{doc_id}/generate-reports")
def generate_reports(request: Request, doc_id: str):
    """生成记录（9.2 报告透传）：各章生成的时间/模型/结果，供 UI「生成记录」展示。"""
    st = state(request)
    DocStore(st.layout, doc_id).manifest()          # 不存在的文档 → 404
    out = []
    d = st.layout.reports_doc_dir(doc_id)
    if d.exists():
        for p in sorted(d.glob("*.generate.json")):
            try:
                out.append(json.loads(p.read_text(encoding="utf-8")))
            except Exception:
                continue                            # 单个报告损坏不影响列表
    out.sort(key=lambda r: str(r.get("created_at", "")), reverse=True)
    return {"reports": out}


@router.get("/api/v1/docs/{doc_id}/pdf")
def get_pdf(request: Request, doc_id: str):
    """PDF 静态服务：支持 HTTP Range（206/416）与完整 GET（200）。"""
    st = state(request)
    store = DocStore(st.layout, doc_id)
    manifest = store.manifest()
    path = (st.layout.root / manifest["pdf_path"]).resolve()
    if not path.exists():
        raise NotFound("PDF 文件不存在")
    return FileResponse(path, media_type="application/pdf", filename=f"{doc_id}.pdf")


@router.get("/api/v1/docs/{doc_id}/figures/{figure_id}")
def get_figure(request: Request, doc_id: str, figure_id: str):
    st = state(request)
    if not FIGURE_ID_RE.match(figure_id):
        raise InputError(f"figure_id 非法: {figure_id}")
    path = st.layout.safe_join(st.layout.doc_dir(doc_id), "figures", f"{figure_id}.png")
    if not path.exists():
        raise NotFound("图资源不存在")
    return FileResponse(path, media_type="image/png")


@router.get("/api/v1/docs/{doc_id}/pages/{page}/blocks")
def page_blocks(request: Request, doc_id: str, page: int):
    """教材→笔记：该页关联块摘要（响应 < 200ms 由索引保证）。"""
    st = state(request)
    rows = queries.blocks_for_page(st.layout, doc_id, page)
    store = DocStore(st.layout, doc_id)
    out = []
    for r in rows:
        block = store.get_block(r["block_id"])
        out.append({"block_id": block["id"], "type": block["type"], "title": block["title"],
                    "summary": (block.get("content") or "")[:120], "status": block["status"]})
    return out


@router.post("/api/v1/docs/{doc_id}/match")
def match_selection(request: Request, doc_id: str, body: dict):
    """选区匹配（6.3.2）；rects 为 PDF 用户空间坐标，v1 仅支持单页选区。"""
    st = state(request)
    try:
        page = int(body.get("page", 0) or 0)
    except (TypeError, ValueError):
        raise InputError("page 必须是整数")
    rects = body.get("rects") or []
    text = body.get("text") or ""
    if page < 1 or not rects:
        raise InputError("请求体必须含合法 page 与 rects")
    # 选区矩形严格校验（6.3.2）：每项 [x0,y0,x1,y1] 有限数值，非法即 400 而非 500
    clean_rects = []
    for r in rects:
        if not isinstance(r, (list, tuple)) or len(r) != 4:
            raise InputError("rects 元素必须是 [x0,y0,x1,y1]")
        try:
            vals = [float(v) for v in r]
        except (TypeError, ValueError):
            raise InputError("rects 必须是数值")
        if not all(math.isfinite(v) for v in vals):
            raise InputError("rects 含非有限数值")
        clean_rects.append(vals)
    rects = clean_rects
    hits = queries.match_selection(st.layout, doc_id, page, rects, text, st.config)
    out = []
    for h in hits:
        store = DocStore(st.layout, doc_id)
        blocks = []
        for bid in h.get("note_block_ids", []) if isinstance(h.get("note_block_ids"), list) else []:
            try:
                b = store.get_block(bid)
                blocks.append({"block_id": b["id"], "title": b["title"], "type": b["type"],
                               "summary": (b.get("content") or "")[:120], "status": b["status"]})
            except Exception:
                continue
        out.append({"anchor_id": h["anchor_id"], "page": h["page"],
                    "bbox": [h["bbox_x0"], h["bbox_y0"], h["bbox_x1"], h["bbox_y1"]],
                    "content": h.get("content", "")[:200], "scores": h.get("scores"),
                    "match": h.get("match"), "blocks": blocks})
    return {"hits": out}


@router.get("/api/v1/search")
def search(request: Request, doc_id: str, q: str, mode: str = "fts"):
    """检索（6.3）：mode=vec 走 sqlite-vec 语义兜底，未命中自动回落 FTS。"""
    st = state(request)
    store = DocStore(st.layout, doc_id)
    top_k = int(st.config.get("match", {}).get("top_k", 8))
    anchors: list[dict] = []
    if mode == "vec":
        from src.anchor.embed import embedder_from_config

        embedder = embedder_from_config(st.config)
        vec = (embedder.encode([q]) or [None])[0]
        if vec:
            anchors = queries.search_semantic(
                st.layout, doc_id, vec, k=top_k,
                threshold=float(st.config.get("embedding", {}).get("threshold", 0.55)))
    if not anchors:
        anchors = queries.search_anchors(st.layout, doc_id, q, limit=top_k)
    blocks = [b for b in store.all_block_rows()
              if q.lower() in (b.get("title", "") + b.get("content", "")).lower()][:8]
    return {
        "anchors": [{"anchor_id": a["anchor_id"], "page": a["page"], "content": a["content"],
                     "note_block_ids": a.get("note_block_ids", []), "match": a.get("match", "fts")}
                    for a in anchors],
        "blocks": [{"block_id": b["id"], "type": b["type"], "title": b["title"],
                    "summary": (b.get("content") or "")[:120], "status": b["status"]} for b in blocks],
        "mode": mode,
    }


@router.post("/api/v1/docs/{doc_id}/generate")
def trigger_generate(request: Request, doc_id: str, body: dict | None = None):
    """触发章节再生成（异步任务）；固定走 5.7 合并策略。"""
    st = state(request)
    body = body or {}
    chapter_id = body.get("chapter_id")
    model = body.get("model")

    def run(req, progress, is_cancelled, register_artifact, task_id=None):
        from src.generate.engine import generate_chapter

        store = DocStore(st.layout, doc_id)
        chapters = [chapter_id] if chapter_id else store.chapter_ids()
        # 并行分章（提速）：整本生成时多章同时跑 outline/fill，写入由引擎内
        # per-doc 锁串行（4.2.2 单写者），不会互撞 RevisionConflict
        workers = 1
        if len(chapters) > 1:
            workers = int(st.config.get("generate", {}).get("chapter_concurrency", 2) or 2)
            workers = max(1, min(workers, len(chapters)))

        # 跨章进度聚合：并行时各章同时上报 outline/fill，直接透传进度条会来回跳，
        # 按章累计后合并成全局 done/total，保证单调推进
        agg: dict[str, dict] = {}
        agg_lock = threading.Lock()

        def make_progress(ch: str):
            def _p(evt: dict) -> None:
                evt = dict(evt or {})
                stage = evt.get("stage")
                with agg_lock:
                    if stage in ("outline", "fill"):
                        agg.setdefault(ch, {})[stage] = evt
                        rows = [x.get(stage) for x in agg.values() if x.get(stage)]
                        if stage == "outline":
                            progress({"stage": "outline",
                                      "batch": sum(r.get("batch") or 0 for r in rows),
                                      "batches": sum(r.get("batches") or 0 for r in rows),
                                      "chapter_id": ch})
                            return
                        progress({"stage": "fill",
                                  "done": sum(r.get("done") or 0 for r in rows),
                                  "total": sum(r.get("total") or 0 for r in rows),
                                  "chapter_id": ch})
                        return
                    evt.setdefault("chapter_id", ch)
                    progress(evt)
            return _p

        def failed_report(ch: str, reason: str) -> dict:
            return {"doc_id": doc_id, "chapter_id": ch, "status": "failed",
                    "failures": [{"stage": "generate", "reason": reason}]}

        def outcome(ch: str) -> dict:
            """执行一章；可隔离的失败转成失败报告，不可隔离的继续上抛。"""
            if is_cancelled():
                raise TaskCancelled("任务已取消")
            progress({"stage": "generate", "chapter_id": ch})
            try:
                return generate_chapter(doc_id, ch, layout=st.layout, client=st.client,
                                        config=st.config, registry=st.registry, model=model,
                                        merge_mode="merge", task_id=task_id,
                                        progress=make_progress(ch), is_cancelled=is_cancelled)
            except TaskCancelled:
                raise
            except PriceUnknown as exc:
                if exc.code != "BUDGET_EXCEEDED":
                    raise        # 配置类（未配置模型/价格未知）→ 整个任务清晰失败
                return failed_report(ch, str(exc))
            except NoteGenError as exc:
                # 4.7 PipelineError 语义：阶段性失败记录后继续其他章节——
                # 一章缺锚点/提交冲突/校验失败不得拖垮整本书的生成任务
                # （原实现只隔离 PipelineError/ConflictError，一章 quote 校验
                # 失败会让后面所有章节连跑的机会都没有）
                return failed_report(ch, str(exc))

        reports: list[dict] = []
        if workers <= 1:
            for ch in chapters:
                reports.append(outcome(ch))
        else:
            from concurrent.futures import ThreadPoolExecutor, as_completed

            by_ch: dict[str, dict] = {}
            pool = ThreadPoolExecutor(max_workers=workers)
            try:
                futs = {pool.submit(outcome, ch): ch for ch in chapters}
                for fut in as_completed(futs):
                    by_ch[futs[fut]] = fut.result()
            finally:
                # 取消/意外异常：不再等未完成章节（它们会在下一个检查点自行退出）
                pool.shutdown(wait=False, cancel_futures=True)
            reports = [by_ch[ch] for ch in chapters if ch in by_ch]
        if reports and all(r.get("status") == "failed" for r in reports):
            # 全军覆没要以 failed 结局收场（10.4 可解释），不能谎报 succeeded
            reasons = "；".join(
                f"{r['chapter_id']}: " + ("; ".join(str(f.get("reason", "")) for f in r.get("failures", []))
                                          or r.get("status"))
                for r in reports)
            raise PipelineError(f"全部章节生成失败：{reasons}"[:600])
        # 章内块级失败（report status=partial）必须反映到任务结局：
        # 原实现无条件返回 succeeded，22/61 块失败时任务盘照样亮绿灯
        return {"reports": reports,
                "partial": any(r.get("status") != "complete" for r in reports)}

    # 防重复触发：同文档已有排队/运行中的生成任务时直接复用（避免重复烧钱）。
    # 收紧为文档级而非章节级：两个生成任务并跑 = 两个写者抢 data_revision（I7 单写者），
    # 后提交者必 RevisionConflict，白烧一章的 LLM 费用（probe3 实测两任务并跑）。
    # 注：单任务内的多章并行是安全的——写入段由引擎内 per-doc 锁串行（4.2.2）。
    for t in st.runtime.list_tasks(doc_id=doc_id):
        if t.get("kind") == "generate" and t.get("status") in ("queued", "running"):
            return JSONResponse({"task_id": t["task_id"]}, status_code=202)

    # 任务显示名：书名 · 章节名（任务栏一眼看出在生成什么）
    try:
        _man = DocStore(st.layout, doc_id).manifest()
        _title = _man.get("title") or doc_id
        _ch = next((c for c in _man.get("chapters", []) if c.get("chapter_id") == chapter_id), None)
        task_name = _title + " · " + ((_ch or {}).get("title") or chapter_id or "整本")
    except Exception:
        task_name = doc_id + (" · " + chapter_id if chapter_id else "")
    task = st.tasks.submit("generate", run, {"doc_id": doc_id, "chapter_id": chapter_id},
                           doc_id=doc_id, idempotency_key=None, name=task_name)
    return JSONResponse({"task_id": task["task_id"]}, status_code=202)
