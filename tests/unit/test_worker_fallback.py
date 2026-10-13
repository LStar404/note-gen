"""解析 worker 韧性回归：落盘失败退回 stdout 契约、坏解释器退回当前解释器。"""

from __future__ import annotations

import json
import sys

from src.ingest import parse_pdf
from src.ingest import parser_workers


def test_worker_write_falls_back_to_stdout(tmp_path, capsys):
    """output_path 不可写（低完整性/锁）时结果不得被吞：stdout 契约兜底。"""
    req = {"output_path": str(tmp_path / "no_such_dir" / "worker_result.json")}
    parser_workers._write({"ok": True, "figures": []}, req)
    out = capsys.readouterr().out
    assert "RESULT_OK" not in out
    assert json.loads(out) == {"ok": True, "figures": []}, out


def test_worker_write_prefers_file(tmp_path, capsys):
    """可写时走文件契约，stdout 只回 RESULT_OK。"""
    target = tmp_path / "worker_result.json"
    parser_workers._write({"ok": True}, {"output_path": str(target)})
    out = capsys.readouterr().out
    assert out == "RESULT_OK"
    assert json.loads(target.read_text(encoding="utf-8")) == {"ok": True}


def test_resolve_worker_python_falls_back_when_probe_fails(monkeypatch):
    """写盘探针不过（如低完整性标签污染的 venv 解释器）→ 退回当前解释器。"""
    monkeypatch.setattr(parse_pdf, "_WORKER_PY_CACHE", {})
    monkeypatch.setattr(parse_pdf, "_worker_can_write", lambda py: False)
    assert parse_pdf.resolve_worker_python(None) == sys.executable


def test_resolve_worker_python_probe_passes_keeps_venv(monkeypatch):
    """探针通过时按 ADR D31 保留 .venv-parsers 专用解释器（本机有 venv 才验）。"""
    from pathlib import Path

    root = Path(parse_pdf.__file__).resolve().parents[2]
    venv = root / ".venv-parsers" / "Scripts" / "python.exe"
    if not venv.exists():
        venv = root / ".venv-parsers" / "bin" / "python"
    if not venv.exists():
        return
    monkeypatch.setattr(parse_pdf, "_WORKER_PY_CACHE", {})
    monkeypatch.setattr(parse_pdf, "_worker_can_write", lambda py: True)
    assert parse_pdf.resolve_worker_python(None) == str(venv)


def test_resolve_worker_python_explicit_wins(monkeypatch):
    monkeypatch.setattr(parse_pdf, "_worker_can_write", lambda py: False)
    assert parse_pdf.resolve_worker_python("D:/custom/python.exe") == "D:/custom/python.exe"
