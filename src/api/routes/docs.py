"""文档/PDF/图/页/选区/检索/生成触发端点（9.2）。"""

from __future__ import annotations

import math
from pathlib import Path

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from src.core.errors import ConflictError, InputError, NotFound
from src.core.paths import DOC_ID_RE, FIGURE_ID_RE
from src.core.store import DocStore
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

    def run(req, progress, is_cancelled, register_artifact):
        progress({"stage": "ingest"})
        manifest = parse_pdf_with_fallback(target, st.layout.ingest_root,
                                           st.config.get("parser", {}).get("default", "auto"),
                                           layout=st.layout, doc_id=doc, profile=profile,
                                           title=title or Path(file.filename).stem,
                                           config=st.config)
        progress({"stage": "anchor"})
        from src.anchor.chunker import chunk_and_anchor

        counts = {}
        for ch in manifest["chapters"]:
            counts[ch["chapter_id"]] = chunk_and_anchor(doc, ch["chapter_id"], layout=st.layout)
        return {"doc_id": doc, "chapters": manifest["chapters"], "anchors": counts}

    task = st.tasks.submit("import", run, {"doc_id": doc, "filename": file.filename},
                           doc_id=doc, idempotency_key=None)
    return JSONResponse({"task_id": task["task_id"], "doc_id": doc}, status_code=202)


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

    def run(req, progress, is_cancelled, register_artifact):
        from src.generate.engine import generate_chapter

        store = DocStore(st.layout, doc_id)
        chapters = [chapter_id] if chapter_id else store.chapter_ids()
        reports = []
        for ch in chapters:
            if is_cancelled():
                break
            progress({"stage": "generate", "chapter_id": ch})
            reports.append(generate_chapter(doc_id, ch, layout=st.layout, client=st.client,
                                            config=st.config, registry=st.registry, model=model,
                                            merge_mode="merge"))
        return {"reports": reports}

    task = st.tasks.submit("generate", run, {"doc_id": doc_id, "chapter_id": chapter_id},
                           doc_id=doc_id, idempotency_key=None)
    return JSONResponse({"task_id": task["task_id"]}, status_code=202)
