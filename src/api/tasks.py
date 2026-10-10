"""后台任务管理（9.2 / 10.4）：导入/导出/生成任务、进度、取消、产物、重启恢复。"""

from __future__ import annotations

import threading
import uuid
from pathlib import Path
from typing import Callable


class TaskCancelled(Exception):
    pass


class TaskManager:
    def __init__(self, runtime):
        self.runtime = runtime
        self._threads: dict[str, threading.Thread] = {}
        self._cancel: set[str] = set()
        self._artifacts: dict[str, Path] = {}
        self._lock = threading.Lock()

    def interrupt_stale(self) -> int:
        return self.runtime.interrupt_running()

    def submit(self, kind: str, fn: Callable[..., dict], request: dict, *,
               doc_id: str | None = None, idempotency_key: str | None = None) -> dict:
        task_id = "task_" + uuid.uuid4().hex[:12]
        task = self.runtime.create_task(task_id, kind, request, doc_id=doc_id,
                                        idempotency_key=idempotency_key)
        if task.get("status") != "queued" or task.get("result") is not None:
            return task
        thread = threading.Thread(target=self._run, args=(task_id, fn, request), daemon=True)
        with self._lock:
            self._threads[task_id] = thread
        thread.start()
        return self.get(task_id)

    def _run(self, task_id: str, fn: Callable, request: dict) -> None:
        self.runtime.update_task(task_id, status="running", progress={"stage": "start"})
        try:
            result = fn(request, progress=lambda p: self.runtime.update_task(task_id, progress=p),
                        is_cancelled=lambda: task_id in self._cancel,
                        register_artifact=lambda name, path: self._artifacts.__setitem__(f"{task_id}:{name}", Path(path)))
            status = "partial" if result.get("partial") else "succeeded"
            self.runtime.update_task(task_id, status=status, result=result,
                                     progress={"stage": "done"})
        except TaskCancelled as exc:
            self.runtime.update_task(task_id, status="cancelled", error={"message": str(exc)})
        except Exception as exc:  # 任务失败必须可解释（10.4）
            self.runtime.update_task(task_id, status="failed", error={"message": str(exc)})

    def get(self, task_id: str) -> dict:
        return self.runtime.get_task(task_id)

    def cancel(self, task_id: str) -> dict:
        with self._lock:
            self._cancel.add(task_id)
        return self.runtime.update_task(task_id, status="cancelled")

    def artifact(self, task_id: str, name: str) -> Path:
        path = self._artifacts.get(f"{task_id}:{name}")
        if path is None or not path.exists():
            raise KeyError(name)
        return path
