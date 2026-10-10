"""解析器适配器单测（离线可跑：probe/映射/回退）。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from src.core.errors import InputError
from src.ingest.parse_pdf import get_adapter
from src.ingest.parser_workers import _bbox_values, _map_type, _pages_meta_from_pdf

ROOT = Path(__file__).resolve().parents[2]


def test_worker_probe_runs_with_stdlib_only():
    proc = subprocess.run([sys.executable, str(ROOT / "src" / "ingest" / "parser_workers.py"),
                           "probe"], capture_output=True, text=True, cwd=str(ROOT))
    assert proc.returncode == 0
    data = json.loads(proc.stdout)
    assert data["ok"] and "modules" in data
    assert "pypdf" in data["modules"]


def test_bbox_values_shapes():
    assert _bbox_values([1, 2, 3, 4]) == [1.0, 2.0, 3.0, 4.0]
    assert _bbox_values((1, 2, 3, 4, 5))[:4] == [1.0, 2.0, 3.0, 4.0]

    class Box:
        bbox = (10, 20, 30, 40)

    assert _bbox_values(Box()) == [10.0, 20.0, 30.0, 40.0]
    assert _bbox_values({"l": 1, "t": 2, "r": 3, "b": 4}) == [1.0, 2.0, 3.0, 4.0]
    assert _bbox_values({"x0": 5, "y0": 6, "x1": 7, "y1": 8}) == [5.0, 6.0, 7.0, 8.0]
    assert _bbox_values(None) is None


def test_map_type():
    assert _map_type("SectionHeader") == ("heading", 2)
    assert _map_type("Equation") == ("formula", 0)
    assert _map_type("Figure") == ("figure", 0)
    assert _map_type("Table") == ("table", 0)
    assert _map_type("Text") == ("paragraph", 0)


def test_pages_meta_fallback_without_pypdf():
    assert _pages_meta_from_pdf("nonexistent.pdf") == []


def test_auto_falls_back_to_pypdf_when_heavy_parsers_missing():
    adapter = get_adapter("auto", python=sys.executable)  # 当前解释器无 marker/docling
    assert adapter.name == "pypdf"


def test_unknown_parser_rejected():
    with pytest.raises(InputError):
        get_adapter("unknown-parser")
