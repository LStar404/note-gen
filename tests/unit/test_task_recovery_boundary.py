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
                        {"doc_id": "d1", "chapter_id": "ch01"}, doc_id="d1",
                        name="样书 · 第 1 章")
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
        assert by_id["task_resume00001"]["name"] == "样书 · 第 1 章", "任务显示名必须持久可取"
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


def test_resume_reuses_same_task_record(workspace):
    """续跑必须原记录原地跑（同 task_id、名字保留），不得另开一条。"""
    import time as _time

    from src.api.tasks import TaskManager

    rt = RuntimeDB(workspace.runtime_db)
    rt.create_task("task_same0000001", "generate",
                   {"doc_id": "d1", "chapter_id": "ch01"},
                   doc_id="d1", name="我的任务")
    rt.update_task("task_same0000001", status="cancelled")
    tm = TaskManager(rt)
    done = {"ok": False}

    def fn(req, progress, is_cancelled, register_artifact, task_id=None):
        done["ok"] = True
        return {"partial": False}

    t = tm.resume("task_same0000001", fn, {"doc_id": "d1", "chapter_id": "ch01"})
    assert t["task_id"] == "task_same0000001", "续跑必须复用原任务记录"
    assert t["name"] == "我的任务", "任务名随记录保留"
    for _ in range(60):
        if done["ok"] and rt.get_task("task_same0000001")["status"] == "succeeded":
            break
        _time.sleep(0.05)
    assert rt.get_task("task_same0000001")["status"] == "succeeded"


def test_delete_running_task_worker_survives(workspace):
    """删除运行中任务后，worker 的进度/收尾写入不得抛 KeyError 炸穿线程。
    （update_task 曾以 get_task 收尾，删行即 KeyError，连 _run 的异常兜底里都会再炸。）"""
    import threading

    from src.api.tasks import TaskManager

    rt = RuntimeDB(workspace.runtime_db)
    tm = TaskManager(rt)
    started, release = threading.Event(), threading.Event()
    errors: list = []
    crashed: list = []
    prev_hook = threading.excepthook
    threading.excepthook = lambda args: crashed.append(args)
    try:
        def fn(request, progress, is_cancelled, register_artifact, task_id=None):
            started.set()
            release.wait(10)
            try:
                progress({"stage": "late"})          # 行已删：必须容忍
            except Exception as exc:                  # noqa: BLE001
                errors.append(exc)
            return {"partial": False}

        task = tm.submit("generate", fn, {})
        thread = tm._threads[task["task_id"]]
        assert started.wait(10)
        tm.delete(task["task_id"])
        release.set()
        thread.join(10)
        assert not thread.is_alive()
        assert not errors, f"删行后进度写入不得抛错: {errors}"
        assert not crashed, f"worker 线程不得裸崩: {crashed}"
    finally:
        threading.excepthook = prev_hook


def test_reset_task_claim_is_atomic(workspace):
    """续跑认领原子性：排队/运行中的任务不得被再次认领（双击续跑防双跑双倍烧钱）。"""
    rt = RuntimeDB(workspace.runtime_db)
    rt.create_task("task_claim000001", "generate", {"doc_id": "d1"}, doc_id="d1")
    rt.update_task("task_claim000001", status="failed")
    assert rt.reset_task("task_claim000001") is True, "已结束任务可认领"
    assert rt.reset_task("task_claim000001") is False, "排队中不得被再次认领"
    rt.update_task("task_claim000001", status="succeeded")
    assert rt.reset_task("task_claim000001") is True, "已结束任务可续跑"
    assert rt.reset_task("task_claim00000000-missing") is False, "不存在的任务认领失败而非抛错"


def test_resume_double_click_starts_only_one(workspace):
    """并发续跑同一任务只能真正启动一次。"""
    import threading
    import time

    from src.api.tasks import TaskManager

    rt = RuntimeDB(workspace.runtime_db)
    rt.create_task("task_dbl00000001", "generate", {"doc_id": "d1"}, doc_id="d1")
    rt.update_task("task_dbl00000001", status="failed")
    tm = TaskManager(rt)
    runs: list = []
    gate = threading.Barrier(2)

    def fn(req, progress, is_cancelled, register_artifact, task_id=None):
        runs.append(1)
        time.sleep(0.2)
        return {"partial": False}

    def racer():
        gate.wait(10)
        try:
            tm.resume("task_dbl00000001", fn, {"doc_id": "d1"})
        except KeyError:
            pass

    threads = [threading.Thread(target=racer) for _ in range(2)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(10)
    time.sleep(0.5)
    assert len(runs) == 1, f"并发续跑只能起一个: {len(runs)}"


def test_cancel_unknown_task_is_404(workspace):
    """取消不存在的任务应 404（曾裸 KeyError → 500）。"""
    app = create_app(workspace.root)
    with TestClient(app, base_url="http://127.0.0.1:8787") as client:
        headers = {"X-Local-Token": app.state.app_state.token_value}
        resp = client.post("/api/v1/tasks/task_ghost000001/cancel", headers=headers)
        assert resp.status_code == 404, resp.text
