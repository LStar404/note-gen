"""任务删除回归：删记录不得删掉导出交付物（曾把 notes_out 里的导出文件一并 unlink）。"""

from __future__ import annotations

import time

from src.api.tasks import TaskManager
from src.db.runtime import get_runtime


def _run_task(tm: TaskManager, fn, kind: str = "export") -> dict:
    task = tm.submit(kind, fn, {})
    for _ in range(200):
        cur = tm.get(task["task_id"])
        if cur.get("status") not in ("queued", "running"):
            return cur
        time.sleep(0.05)
    raise AssertionError("任务未在预期时间内结束")


def test_delete_task_keeps_export_artifacts(workspace):
    rt = get_runtime(workspace.runtime_db)
    tm = TaskManager(rt)
    out = workspace.notes_dir / "deliverable.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("导出内容", encoding="utf-8")

    def fn(req, progress, is_cancelled, register_artifact, task_id=None):
        register_artifact(out.name, str(out))
        return {"paths": [str(out)]}

    done = _run_task(tm, fn)
    assert done["status"] == "succeeded", done
    assert tm.artifact(done["task_id"], out.name) == out      # 产物可下载

    assert tm.delete(done["task_id"]) is True
    assert out.exists(), \
        "删除任务记录不得删除导出文件（交付物归用户，在 notes_out/<doc>/ 下）"
