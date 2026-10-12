"""后台任务管理（9.2 / 10.4）：导入/导出/生成任务、进度、取消、产物、重启恢复。"""

from __future__ import annotations

import threading
import uuid
from pathlib import Path
from typing import Callable

from src.core.errors import TaskCancelled


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
               doc_id: str | None = None, idempotency_key: str | None = None,
               name: str = "") -> dict:
        task_id = "task_" + uuid.uuid4().hex[:12]
        task = self.runtime.create_task(task_id, kind, request, doc_id=doc_id,
                                        idempotency_key=idempotency_key, name=name)
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
                        register_artifact=lambda name, path: self._artifacts.__setitem__(f"{task_id}:{name}", Path(path)),
                        task_id=task_id)
            status = "partial" if result.get("partial") else "succeeded"
            if self.get(task_id).get("status") != "cancelled":
                # 取消后 fn 仍正常返回时不改写结局：cancelled 是真实结局
                self.runtime.update_task(task_id, status=status, result=result,
                                         progress={"stage": "done"})
        except TaskCancelled as exc:
            self.runtime.update_task(task_id, status="cancelled", error={"message": str(exc)})
        except Exception as exc:  # 任务失败必须可解释（10.4）
            self.runtime.update_task(task_id, status="failed", error={"message": str(exc)})

    def get(self, task_id: str) -> dict:
        return self.runtime.get_task(task_id)

    def cancel(self, task_id: str) -> dict:
        task = self.runtime.get_task(task_id)
        if task.get("status") not in ("queued", "running"):
            return task          # 已结束任务保持真实结局，不被改写为 cancelled
        with self._lock:
            self._cancel.add(task_id)
        return self.runtime.update_task(task_id, status="cancelled")

    def resume(self, task_id: str, fn: Callable[..., dict], request: dict) -> dict:
        """续跑：复用原任务记录（同 task_id，名字/参数不变），不新开记录。"""
        task = self.runtime.get_task(task_id)
        if task.get("status") in ("queued", "running"):
            return task
        self.runtime.reset_task(task_id)
        thread = threading.Thread(target=self._run, args=(task_id, fn, request), daemon=True)
        with self._lock:
            self._cancel.discard(task_id)
            self._threads[task_id] = thread
        thread.start()
        return self.get(task_id)

    def delete(self, task_id: str) -> bool:
        """彻底删除任务记录（无回收站、不留副本）：运行中的先置取消，
        任务行 + 产物文件一并清除。"""
        task = self.runtime.get_task(task_id)
        if task.get("status") in ("queued", "running"):
            with self._lock:
                self._cancel.add(task_id)
            self.runtime.update_task(task_id, status="cancelled")
        with self._lock:
            self._threads.pop(task_id, None)
            # 产物文件也是副本：一并删除，不留残余
            for key in [k for k in list(self._artifacts) if k.startswith(task_id + ":")]:
                path = self._artifacts.pop(key, None)
                try:
                    if path is not None:
                        Path(path).unlink(missing_ok=True)
                except OSError:
                    pass
        return self.runtime.delete_task(task_id)

    def artifact(self, task_id: str, name: str) -> Path:
        path = self._artifacts.get(f"{task_id}:{name}")
        if path is None or not path.exists():
            raise KeyError(name)
        return path
