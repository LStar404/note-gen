"""JSON/JSONL 原子读写（规范 4.2）：temp + fsync + os.replace，UTF-8 无 BOM、LF 换行。"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterable

from .errors import DataCorrupt, NotFound


def canonical_json(obj: Any) -> str:
    """规范 JSON 编码：键排序、紧凑分隔、UTF-8（5.3.4 缓存/指纹用）。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def sha256_file(path: str | Path) -> str | None:
    p = Path(path)
    if not p.exists():
        return None
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def dump_line(obj: Any) -> str:
    """单行 JSON 对象（字符串内换行由 JSON 转义）。"""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _fsync_file(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write_bytes(path: str | Path, data: bytes) -> None:
    """整文件原子替换：同目录临时文件 + fsync + os.replace（Windows 同卷）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    try:
        _fsync_file(path)
    except OSError:
        pass
    _fsync_dir(path.parent)


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def rewrite_json(path: str | Path, obj: Any) -> None:
    text = json.dumps(obj, ensure_ascii=False, indent=2) + "\n"
    atomic_write_bytes(path, text.encode("utf-8"))


def rewrite_jsonl(path: str | Path, lines: Iterable[Any]) -> None:
    """整文件重写（anchors/blocks 用）。"""
    body = "".join(dump_line(x) + "\n" for x in lines)
    atomic_write_bytes(path, body.encode("utf-8"))


def append_jsonl(path: str | Path, obj: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(dump_line(obj) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def load_json(path: str | Path, *, required: bool = True) -> Any:
    p = Path(path)
    if not p.exists():
        if required:
            raise NotFound(f"缺少文件: {p}")
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def load_jsonl(path: str | Path, *, strict: bool = False, on_warn: Callable[[str], None] | None = None) -> list[dict]:
    """读取 JSONL。

    宽松模式（默认）：末行截断/损坏时丢弃该行并告警（载入容错，4.2.2 第 4 条）；
    严格模式：任何损坏抛 DataCorrupt——写入前校验必须用严格模式，
    不得丢弃末行后覆盖原文件。
    """
    p = Path(path)
    if not p.exists():
        return []
    raw = p.read_text(encoding="utf-8")
    out: list[dict] = []
    lines = raw.split("\n")
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            is_last = all(not x.strip() for x in lines[i + 1:])
            if not strict and is_last:
                if on_warn:
                    on_warn(f"{p}: 末行损坏已忽略（{exc}）")
                break
            raise DataCorrupt(f"{p}: 第 {i + 1} 行 JSON 损坏: {exc}")
        if not isinstance(obj, dict):
            raise DataCorrupt(f"{p}: 第 {i + 1} 行不是 JSON 对象")
        out.append(obj)
    return out


def update_line(path: str | Path, key: str, value: Any, updater: Callable[[dict], dict | None]) -> list[dict]:
    """候选内按主键更新；返回新候选列表，最终经提交器落盘（3.3 core 契约）。"""
    rows = load_jsonl(path, strict=True)
    out: list[dict] = []
    hit = False
    for row in rows:
        if row.get(key) == value:
            hit = True
            new_row = updater(dict(row))
            if new_row is not None:
                out.append(new_row)
            continue
        out.append(row)
    if not hit:
        raise NotFound(f"{path}: 未找到 {key}={value}")
    return out
