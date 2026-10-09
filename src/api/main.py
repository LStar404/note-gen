"""FastAPI 应用工厂（7.5/9.2）：生产同源托管 viewer/dist，开发经 Vite 代理。"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.staticfiles import StaticFiles

from src.core.errors import NoteGenError

from .routes import blocks as blocks_routes
from .routes import docs as docs_routes
from .routes import system as system_routes
from .security import LocalSecurityMiddleware
from .state import AppState

ROOT = Path(os.environ.get("NOTE_GEN_ROOT", Path(__file__).resolve().parents[2]))


def create_app(root: str | Path | None = None) -> FastAPI:
    root = Path(root or ROOT).resolve()
    state = AppState(root)
    app = FastAPI(title="note-gen", version="0.1.0")
    app.state.app_state = state

    cfg_port = int(state.config.get("app", {}).get("port", 8787))
    allowed_hosts = {f"127.0.0.1:{cfg_port}", f"localhost:{cfg_port}",
                     "127.0.0.1", "localhost"}
    allowed_origins = set(state.config.get("app", {}).get("cors_origins", []) or [])
    allowed_origins |= {f"http://127.0.0.1:{cfg_port}", f"http://localhost:{cfg_port}",
                        f"http://127.0.0.1:5173", f"http://localhost:5173"}
    app.add_middleware(LocalSecurityMiddleware, token=state.token_value,
                       allowed_hosts=allowed_hosts, allowed_origins=allowed_origins)

    @app.exception_handler(NoteGenError)
    async def _handle_error(request: Request, exc: NoteGenError):
        return JSONResponse(exc.to_dict(), status_code=exc.http_status)

    app.include_router(docs_routes.router)
    app.include_router(blocks_routes.router)
    app.include_router(system_routes.router)

    dist = root / "viewer" / "dist"
    if dist.exists():
        app.mount("/assets", StaticFiles(directory=str(dist / "assets")), name="assets")

        @app.get("/", response_class=HTMLResponse)
        async def index():
            html = (dist / "index.html").read_text(encoding="utf-8")
            meta = f'<meta name="local-token" content="{state.token_value}">'
            html = html.replace("<head>", "<head>" + meta, 1)
            return HTMLResponse(html, headers={"Cache-Control": "no-store"})

        @app.get("/{path:path}")
        async def static_file(path: str):
            target = state.layout.safe_join(dist, *path.split("/")) if path else dist / "index.html"
            if target.is_file():
                from fastapi.responses import FileResponse

                return FileResponse(target)
            html = (dist / "index.html").read_text(encoding="utf-8")
            meta = f'<meta name="local-token" content="{state.token_value}">'
            return HTMLResponse(html.replace("<head>", "<head>" + meta, 1))
    else:

        @app.get("/", response_class=HTMLResponse)
        async def index_fallback():
            return HTMLResponse(
                "<html><body><h1>note-gen</h1>"
                "<p>前端未构建：请运行 <code>cd viewer &amp;&amp; pnpm install &amp;&amp; pnpm build</code></p>"
                f'<p>API 已就绪，本地 token：<code>{state.token_value[:6]}…</code></p>'
                "</body></html>")

    return app


app = create_app()


def run_server(host: str | None = None, port: int | None = None) -> None:
    import uvicorn

    cfg = AppState(ROOT).config.get("app", {})
    uvicorn.run("src.api.main:app", host=host or cfg.get("host", "127.0.0.1"),
                port=int(port or cfg.get("port", 8787)), reload=False)
