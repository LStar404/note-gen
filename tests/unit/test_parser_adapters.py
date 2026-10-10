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


def test_doc_namespace_collision_rejected(tmp_path):
    """规范 5.2：doc_ns 碰撞必须拒绝导入（真实碰撞对：sha256 前 8 位相同）。"""
    from src.core.paths import Layout
    from src.core.text import doc_namespace
    from src.ingest.parse_pdf import parse_pdf
    from tests.fixtures.make_sample_pdf import build_sample_pdf

    a, b = "doc-x2935", "doc-x30382"          # 暴力求得的真碰撞对（ns=f96e00f4）
    assert doc_namespace(a) == doc_namespace(b)

    pdf = build_sample_pdf(tmp_path / "a.pdf")
    layout = Layout(tmp_path).ensure_base()
    parse_pdf(pdf, layout.ingest_root, "pypdf", layout=layout, doc_id=a, title="A")
    with pytest.raises(InputError) as ei:
        parse_pdf(pdf, layout.ingest_root, "pypdf", layout=layout, doc_id=b, title="B")
    assert getattr(ei.value, "code", "") == "DOC_NS_COLLISION"

    # 不同命名空间正常导入
    parse_pdf(pdf, layout.ingest_root, "pypdf", layout=layout, doc_id="doc-unique-1", title="C")


def test_fallback_does_not_mask_input_errors(monkeypatch, tmp_path):
    """解析回退只应处理解析器失败；InputError 必须直接报错即止（不得被兜底掩盖）。"""
    import src.ingest.parse_pdf as pp
    from src.core.errors import InputError as IE, PipelineError as PE

    calls = []

    def fake_parse(pdf_path, out_dir, parser="auto", **kw):
        calls.append(parser)
        if parser == "marker":
            raise IE("doc_id 非法")
        return {"doc_id": "x"}

    monkeypatch.setattr(pp, "parse_pdf", fake_parse)
    with pytest.raises(IE):
        pp.parse_pdf_with_fallback("a.pdf", tmp_path, "marker")
    assert calls == ["marker"], "InputError 不应回退重试"

    calls.clear()

    def fake_parse2(pdf_path, out_dir, parser="auto", **kw):
        calls.append(parser)
        if parser == "marker":
            raise PE("解析器崩溃")
        return {"doc_id": "x", "parser_fallback": True}

    monkeypatch.setattr(pp, "parse_pdf", fake_parse2)
    out = pp.parse_pdf_with_fallback("a.pdf", tmp_path, "marker")
    assert out["parser_fallback"] is True
    assert calls == ["marker", "pypdf"], "解析器失败应回退到 pypdf"
