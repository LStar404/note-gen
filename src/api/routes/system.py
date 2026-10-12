"""模型管理、用量、导出与任务端点（9.2）。"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse

from src.core.errors import InputError, NotFound

router = APIRouter()


def state(request: Request):
    return request.app.state.app_state


@router.get("/api/v1/models")
def list_models(request: Request):
    st = state(request)
    return {"providers": [st.registry.masked_provider(p) for p in st.registry.providers()],
            "routes": st.config.get("generate", {}).get("models", {})}


@router.post("/api/v1/models/test")
def test_model(request: Request, body: dict):
    st = state(request)
    provider_id = body.get("provider_id")
    model = body.get("model")
    if not provider_id or not model:
        raise InputError("需要 provider_id 与 model")
    return st.client.test_connection(provider_id, model)


@router.put("/api/v1/models/{provider_id}")
def upsert_provider(request: Request, provider_id: str, body: dict):
    st = state(request)
    body["id"] = provider_id
    st.registry.upsert_provider(body)
    # 换 key / 改 base_url 必须重建客户端：适配器把密钥固化在实例里，
    # 不重建则要重启服务才生效（用户实测「换了 key 仍无效」的直接原因）
    st.invalidate_client()
    return st.registry.masked_provider(st.registry.provider(provider_id))


@router.delete("/api/v1/models/{provider_id}")
def delete_provider(request: Request, provider_id: str):
    st = state(request)
    st.registry.delete_provider(provider_id, st.routed_models())
    st.invalidate_client()
    return {"deleted": provider_id}


@router.put("/api/v1/model-routes")
def update_routes(request: Request, body: dict):
    st = state(request)
    for slot, name in (body or {}).items():
        if name:
            st.registry.find_model(name)   # 校验模型存在
            if slot == "agent":
                caps = st.registry.capabilities(name)
                if not caps.get("tools"):
                    raise InputError(f"模型 {name} 缺少 tools 能力，不能用于 agent 任务")
    st.save_model_routes(body or {})
    st.invalidate_client()
    return {"routes": st.config.get("generate", {}).get("models", {})}


@router.get("/api/v1/settings")
def get_settings(request: Request):
    """应用设置（解析器/OCR/输出语言/导出编码/生成参数）。"""
    st = state(request)
    cfg = st.config
    return {
        "parser": cfg.get("parser", {}),
        "generate": {k: v for k, v in (cfg.get("generate", {}) or {}).items() if k != "models"},
        "export": cfg.get("export", {}),
        "match": cfg.get("match", {}),
        "history": cfg.get("history", {}),
        "embedding": cfg.get("embedding", {}),
        "options": {
            "parsers": ["auto", "marker", "docling", "pypdf", "pymupdf"],
            "ocr_modes": ["auto", "force", "off"],
            "languages": ["zh", "en", "ja"],
            "encodings": ["utf-8", "utf-8-sig", "gbk", "big5"],
        },
    }


@router.put("/api/v1/settings")
def put_settings(request: Request, body: dict):
    """更新应用设置（原子保存 config.yaml，不涉及密钥）。"""
    from src.core.config import (ENCODINGS, OUTPUT_LANGUAGES, normalize_settings,
                                 save_config)

    st = state(request)
    cfg = st.config
    for section in ("parser", "generate", "export", "match", "history", "embedding"):
        if section in body and isinstance(body[section], dict):
            cfg[section] = {**(cfg.get(section) or {}), **body[section]}

    parser = cfg.get("parser", {})
    if parser.get("default") not in ("auto", "marker", "docling", "pypdf", "pymupdf"):
        raise InputError(f"不支持的解析器: {parser.get('default')}")
    if parser.get("ocr_mode") not in ("auto", "force", "off"):
        raise InputError(f"不支持的 ocr_mode: {parser.get('ocr_mode')}")
    cfg = normalize_settings(cfg)
    if cfg["export"]["encoding"] not in ENCODINGS:
        raise InputError(f"不支持的编码: {cfg['export']['encoding']}")
    if cfg["generate"]["output_language"] not in OUTPUT_LANGUAGES:
        raise InputError(f"不支持的输出语言: {cfg['generate']['output_language']}")

    save_config(st.root, cfg)
    st.reload_config()
    return {"settings": {"parser": st.config.get("parser"),
                         "generate": st.config.get("generate"),
                         "export": st.config.get("export")}}


@router.get("/api/v1/usage")
def usage(request: Request, doc_id: str | None = None, days: int = 30):
    st = state(request)
    return {"rows": st.runtime.usage_summary(doc_id=doc_id, days=days),
            "total_cost_est": st.runtime.usage_total(doc_id=doc_id),
            "note": "成本为估算值，未知价格显示为未知，不按 0 计"}


@router.post("/api/v1/export")
def trigger_export(request: Request, body: dict):
    st = state(request)
    doc_id = body.get("doc_id")
    chapter_id = body.get("chapter_id")
    fmt = body.get("format", "md")
    if fmt not in ("md", "tex", "both", "md-obsidian", "anki"):
        raise InputError(f"不支持的导出格式: {fmt}")
    # 导出语言/编码：请求可覆盖，默认取全局设置
    lang = body.get("language") or st.config.get("generate", {}).get("output_language", "zh")
    encoding = body.get("encoding") or st.config.get("export", {}).get("encoding", "utf-8")

    def run(req, progress, is_cancelled, register_artifact, task_id=None):
        from src.core.store import DocStore
        from src.export.anki_export import export_anki
        from src.export.md_export import export_md_book, export_md_chapter, export_md_obsidian
        from src.export.tex_export import export_tex_book, export_tex_chapter

        store = DocStore(st.layout, doc_id)
        out_dir = st.layout.notes_doc_dir(doc_id)
        paths = []
        chapters = [chapter_id] if chapter_id else [c["chapter_id"] for c in store.manifest()["chapters"]]
        for ch in chapters:
            progress({"stage": "export", "chapter_id": ch})
            if fmt in ("md", "both"):
                paths.append(str(export_md_chapter(store, ch, out_dir=out_dir,
                                                   lang=lang, encoding=encoding)))
            if fmt in ("tex", "both"):
                paths.append(str(export_tex_chapter(store, ch, out_dir=out_dir,
                                                    lang=lang, encoding=encoding)))
            if fmt == "md-obsidian":
                paths.append(str(export_md_obsidian(store, ch, out_dir=out_dir.parent,
                                                    lang=lang, encoding=encoding)))
            if fmt == "anki":
                paths.append(str(export_anki(store, ch, out_dir=out_dir,
                                             lang=lang, encoding=encoding)))
        if chapter_id is None:
            if fmt in ("md", "both"):
                paths.append(str(export_md_book(store, out_dir=out_dir,
                                                lang=lang, encoding=encoding)))
            if fmt in ("tex", "both"):
                paths.append(str(export_tex_book(store, out_dir=out_dir,
                                                 lang=lang, encoding=encoding)))
            if fmt == "anki":
                paths.append(str(export_anki(store, None, out_dir=out_dir,
                                             lang=lang, encoding=encoding)))
        for p in paths:
            register_artifact(Path(p).name, p)
        return {"paths": paths}

    task = st.tasks.submit("export", run, {"doc_id": doc_id, "chapter_id": chapter_id, "format": fmt},
                           doc_id=doc_id)
    return JSONResponse({"task_id": task["task_id"]}, status_code=202)


@router.get("/api/v1/tasks")
def list_tasks(request: Request, doc_id: str | None = None, limit: int = 20):
    """最近任务（持久化于 runtime.db）：刷新/重启后恢复任务记录与续跑入口。"""
    st = state(request)
    items = st.runtime.list_tasks(doc_id=doc_id)
    return {"items": items[: max(1, min(int(limit), 100))]}


@router.get("/api/v1/tasks/{task_id}")
def get_task(request: Request, task_id: str):
    st = state(request)
    try:
        return st.tasks.get(task_id)
    except KeyError:
        raise NotFound(f"任务不存在: {task_id}")


@router.delete("/api/v1/tasks/{task_id}")
def delete_task(request: Request, task_id: str):
    """删除任务记录（清理不需要的生成项目）；运行中的先优雅取消再删除。"""
    st = state(request)
    try:
        st.tasks.delete(task_id)
    except KeyError:
        raise NotFound(f"任务不存在: {task_id}")
    return {"deleted": True, "task_id": task_id}


@router.post("/api/v1/tasks/{task_id}/cancel")
def cancel_task(request: Request, task_id: str):
    st = state(request)
    return st.tasks.cancel(task_id)


@router.get("/api/v1/tasks/{task_id}/artifacts/{name}")
def task_artifact(request: Request, task_id: str, name: str):
    st = state(request)
    try:
        path = st.tasks.artifact(task_id, name)
    except KeyError:
        raise NotFound(f"产物不存在: {name}")
    return FileResponse(path)
