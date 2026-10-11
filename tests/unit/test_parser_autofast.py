"""解析器自动分流回归：数字版 PDF 必须走 pypdf 快速路径（导入慢的根因）。"""

from __future__ import annotations

from src.ingest.parse_pdf import _has_text_layer, parse_pdf
from tests.fixtures.make_sample_pdf import build_sample_pdf


def test_auto_parser_prefers_pypdf_for_digital_pdf(workspace, tmp_path):
    """auto + 有文字层 → pypdf（秒级）；不得默认走 marker/docling（百页数十分钟）。"""
    pdf = build_sample_pdf(tmp_path / "digital.pdf")
    events: list[dict] = []
    manifest = parse_pdf(pdf, workspace.ingest_root, "auto", layout=workspace,
                         doc_id="fast-doc", title="Fast", progress=events.append)
    assert manifest["parser"]["name"] == "pypdf", manifest["parser"]
    assert any(e.get("pages_total") for e in events), "必须上报页级进度（进度条数据源）"
    assert any(e.get("stage") == "ingest" for e in events)


def test_text_layer_probe_detects_scanned(workspace, tmp_path):
    """无文字层的扫描页（空白页）→ 探测为 False，auto 才会考虑重型解析器。"""
    import pypdf

    pdf = build_sample_pdf(tmp_path / "digital.pdf")
    reader = pypdf.PdfReader(str(pdf))
    assert _has_text_layer(reader) is True

    blank = tmp_path / "blank.pdf"
    blank.write_bytes(build_sample_pdf_bytes_with_blank_pages())
    r2 = pypdf.PdfReader(str(blank))
    assert _has_text_layer(r2) is False


def build_sample_pdf_bytes_with_blank_pages() -> bytes:
    """两页空白 PDF（无文字层），模拟扫描书。"""
    import io

    buf = io.BytesIO()
    import pypdf

    w = pypdf.PdfWriter()
    w.add_blank_page(width=595, height=842)
    w.add_blank_page(width=595, height=842)
    w.write(buf)
    return buf.getvalue()
