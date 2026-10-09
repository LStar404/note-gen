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
    return st.registry.masked_provider(st.registry.provider(provider_id))


@router.delete("/api/v1/models/{provider_id}")
def delete_provider(request: Request, provider_id: str):
    st = state(request)
    st.registry.delete_provider(provider_id, st.routed_models())
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
    return {"routes": st.config.get("generate", {}).get("models", {})}


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
    if fmt not in ("md", "tex", "both", "md-obsidian"):
        raise InputError(f"不支持的导出格式: {fmt}")

    def run(req, progress, is_cancelled, register_artifact):
        from src.core.store import DocStore
        from src.export.md_export import export_md_book, export_md_chapter, export_md_obsidian
        from src.export.tex_export import export_tex_book, export_tex_chapter

        store = DocStore(st.layout, doc_id)
        out_dir = st.layout.notes_doc_dir(doc_id)
        paths = []
        chapters = [chapter_id] if chapter_id else [c["chapter_id"] for c in store.manifest()["chapters"]]
        for ch in chapters:
            progress({"stage": "export", "chapter_id": ch})
            if fmt in ("md", "both"):
                paths.append(str(export_md_chapter(store, ch, out_dir=out_dir)))
            if fmt in ("tex", "both"):
                paths.append(str(export_tex_chapter(store, ch, out_dir=out_dir)))
            if fmt == "md-obsidian":
                paths.append(str(export_md_obsidian(store, ch, out_dir=out_dir.parent)))
        if chapter_id is None:
            if fmt in ("md", "both"):
                paths.append(str(export_md_book(store, out_dir=out_dir)))
            if fmt in ("tex", "both"):
                paths.append(str(export_tex_book(store, out_dir=out_dir)))
        for p in paths:
            register_artifact(Path(p).name, p)
        return {"paths": paths}

    task = st.tasks.submit("export", run, {"doc_id": doc_id, "chapter_id": chapter_id, "format": fmt},
                           doc_id=doc_id)
    return JSONResponse({"task_id": task["task_id"]}, status_code=202)


@router.get("/api/v1/tasks/{task_id}")
def get_task(request: Request, task_id: str):
    st = state(request)
    try:
        return st.tasks.get(task_id)
    except KeyError:
        raise NotFound(f"任务不存在: {task_id}")


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
