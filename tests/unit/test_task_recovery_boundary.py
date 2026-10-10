"""进程重启恢复的边界（回归：构造 AppState 曾误杀正在跑的任务）。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.api.main import create_app
from src.api.state import AppState
from src.db.runtime import RuntimeDB


def _make_running_task(root) -> str:
    rt = RuntimeDB(root)
    task = rt.create_task("task_alive000001", "generate", {"doc_id": "d", "chapter_id": "ch01"})
    rt.update_task(task["task_id"], status="running")
    return task["task_id"]


def test_construction_does_not_interrupt_running_tasks(workspace):
    """AppState 构造（含测试/import 路径）不得把在跑任务打成 interrupted。"""
    tid = _make_running_task(workspace.runtime_db)
    AppState(workspace.root)                      # 构造即恢复 → 会误杀
    rt = RuntimeDB(workspace.runtime_db)
    assert rt.get_task(tid)["status"] == "running", "构造 AppState 不得中断在跑任务"


def test_server_startup_recovers_stale_tasks(workspace):
    """服务真正启动时才做重启恢复（10.4）。"""
    tid = _make_running_task(workspace.runtime_db)
    app = create_app(workspace.root)
    with TestClient(app, base_url="http://127.0.0.1:8787"):   # startup 钩子触发
        pass
    rt = RuntimeDB(workspace.runtime_db)
    assert rt.get_task(tid)["status"] == "interrupted", "启动时应恢复遗留任务"
