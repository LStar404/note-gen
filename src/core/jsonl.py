"""JSON/JSONL 原子读写（规范 4.2）：temp + fsync + os.replace，UTF-8 无 BOM、LF 换行。"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterable

from .errors import DataCorrupt, NotFound

# 行级 I/O 用 orjson（Rust）：解析 3x / 序列化 4.7x（见 scripts/bench_lowlevel.py）。
# canonical_json 仍用 stdlib：它是缓存键/指纹的规范编码，保持跨版本稳定。
try:
    import orjson as _fastjson

    def _loads(raw: bytes | str):
        return _fastjson.loads(raw)

    def _dumps(obj) -> bytes:
        return _fastjson.dumps(obj)

except ImportError:          # 未安装 orjson 时保持原行为
    def _loads(raw: bytes | str):
        return json.loads(raw)

    def _dumps(obj) -> bytes:
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def loads_line(raw: bytes | str) -> Any:
    """单行 JSON 解析（orjson 快路径）。"""
    return _loads(raw)


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
    return _dumps(obj).decode("utf-8")


def atomic_write_bytes(path: str | Path, data: bytes, *, fsync: bool = True) -> None:
    """整文件原子替换：同目录临时文件 + fsync + os.replace（Windows 同卷）。

    fsync=False 供派生/可重建数据的批量写（ingest 页 JSON 逐文件三重刷盘实测 2.3s/书），
    崩溃后重跑即重建，不值得付逐文件刷盘；数据文件（blocks/anchors/manifest）保持默认。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            if fsync:
                os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    if not fsync:
        return
    # 注意：os.replace 之后**不再**对目标文件 fsync——数据已在临时文件句柄上 fsync 过
    # （上面的 flush + os.fsync），替换只改目录项；重复刷同一个 inode 纯属浪费
    # （NTFS 每次 fsync 约 10ms，单次块编辑 8 个文件 ≈ 160ms）。
    # 落盘顺序仍是 POSIX 推荐三步：写临时并 fsync → rename → fsync 目录。
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
    """整文件重写（anchors/blocks 用）；批量序列化后一次写盘。"""
    parts = [_dumps(x) + b"\n" for x in lines]
    atomic_write_bytes(path, b"".join(parts))


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
    return _loads(p.read_bytes())


def load_jsonl(path: str | Path, *, strict: bool = False, on_warn: Callable[[str], None] | None = None) -> list[dict]:
    """读取 JSONL。

    宽松模式（默认）：末行截断/损坏时丢弃该行并告警（载入容错，4.2.2 第 4 条）；
    严格模式：任何损坏抛 DataCorrupt——写入前校验必须用严格模式，
    不得丢弃末行后覆盖原文件。
    """
    p = Path(path)
    if not p.exists():
        return []
    raw = p.read_bytes()
    out: list[dict] = []
    lines = raw.split(b"\n")
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            obj = _loads(line)
        except Exception as exc:
            is_last = all(not x.strip() for x in lines[i + 1:])
            if not strict and is_last:
                if on_warn:
                    on_warn(f"{p}: 末行损坏已忽略（{exc}）")
                break
            raise DataCorrupt(f"{p}: 第 {i + 1} 行 JSON 损坏: {exc}")  # noqa: B904
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
