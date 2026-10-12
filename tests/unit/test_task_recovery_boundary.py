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


def test_list_tasks_endpoint_returns_persisted_records_with_params(workspace):
    """任务列表（供「继续生成」续跑）：记录与 request 参数必须持久可取。"""
    rt = RuntimeDB(workspace.runtime_db)
    t1 = rt.create_task("task_resume00001", "generate",
                        {"doc_id": "d1", "chapter_id": "ch01"}, doc_id="d1")
    rt.update_task(t1["task_id"], status="cancelled")
    rt.create_task("task_resume00002", "generate",
                   {"doc_id": "d1", "chapter_id": None}, doc_id="d1")
    app = create_app(workspace.root)
    with TestClient(app, base_url="http://127.0.0.1:8787") as client:
        headers = {"X-Local-Token": app.state.app_state.token_value}
        resp = client.get("/api/v1/tasks?doc_id=d1", headers=headers)
        assert resp.status_code == 200
        items = resp.json()["items"]
        by_id = {t["task_id"]: t for t in items}
        assert "task_resume00001" in by_id and "task_resume00002" in by_id
        assert by_id["task_resume00001"]["request"] == {"doc_id": "d1", "chapter_id": "ch01"}, \
            "续跑参数必须原样可取"
        assert by_id["task_resume00001"]["status"] == "cancelled"
        resp2 = client.get("/api/v1/tasks", headers=headers)
        assert resp2.status_code == 200, "无 doc_id 过滤也应可用"


def test_delete_task_is_hard_delete_without_leftovers(workspace):
    """删除任务记录必须彻底：get 报不存在、列表无残留（无回收站/软删除）。"""
    rt = RuntimeDB(workspace.runtime_db)
    rt.create_task("task_del00000001", "generate",
                   {"doc_id": "d1", "chapter_id": "ch01"}, doc_id="d1")
    app = create_app(workspace.root)
    with TestClient(app, base_url="http://127.0.0.1:8787") as client:
        headers = {"X-Local-Token": app.state.app_state.token_value}
        resp = client.delete("/api/v1/tasks/task_del00000001", headers=headers)
        assert resp.status_code == 200 and resp.json()["deleted"] is True
        assert client.get("/api/v1/tasks/task_del00000001", headers=headers).status_code == 404, \
            "删除后任务必须不存在（硬删除）"
        items = client.get("/api/v1/tasks?doc_id=d1", headers=headers).json()["items"]
        assert all(t["task_id"] != "task_del00000001" for t in items), "列表不得残留已删记录"
        resp2 = client.delete("/api/v1/tasks/task_del00000001", headers=headers)
        assert resp2.status_code == 404, "重复删除应报不存在"
