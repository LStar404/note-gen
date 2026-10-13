"""打开路径端点回归：只开工作区内路径；越界拒绝、文件开父目录。"""

from __future__ import annotations

import os
import sys

import pytest
from fastapi.testclient import TestClient

from src.api.main import create_app


@pytest.mark.skipif(sys.platform != "win32", reason="打开文件管理器分支按 Windows 验证")
def test_open_path_validates_scope(workspace, monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr(os, "startfile", lambda p: opened.append(str(p)))
    app = create_app(workspace.root)
    with TestClient(app, base_url="http://127.0.0.1:8787") as client:
        headers = {"X-Local-Token": app.state.app_state.token_value}
        r = client.post("/api/v1/system/open-path", json={"path": "data"}, headers=headers)
        assert r.status_code == 200, r.text
        assert opened and opened[0].endswith("data"), opened

        # 文件 → 打开其所在目录（避免 .apkg 被关联程序拉起）
        (workspace.root / "demo.txt").write_text("x", encoding="utf-8")
        opened.clear()
        r = client.post("/api/v1/system/open-path", json={"path": "demo.txt"}, headers=headers)
        assert r.status_code == 200, r.text
        assert opened and opened[0] == str(workspace.root), opened

        # 越界路径拒绝（防任意路径探测）
        r = client.post("/api/v1/system/open-path", json={"path": "..\\..\\Windows"}, headers=headers)
        assert r.status_code >= 400, r.text
        # 不存在/缺参
        r = client.post("/api/v1/system/open-path", json={"path": "nope"}, headers=headers)
        assert r.status_code == 404, r.text
        r = client.post("/api/v1/system/open-path", json={}, headers=headers)
        assert r.status_code >= 400, r.text
