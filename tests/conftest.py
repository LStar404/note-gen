"""测试公共夹具：临时工作区、黄金样本 PDF、已锚定的示例文档。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.anchor.chunker import chunk_and_anchor  # noqa: E402
from src.core.paths import Layout  # noqa: E402
from src.core.store import DocStore  # noqa: E402
from src.ingest.parse_pdf import parse_pdf  # noqa: E402
from tests.fixtures.make_sample_pdf import build_sample_pdf  # noqa: E402


@pytest.fixture()
def workspace(tmp_path: Path) -> Layout:
    return Layout(tmp_path).ensure_base()


@pytest.fixture()
def sample_pdf(tmp_path: Path) -> Path:
    return build_sample_pdf(tmp_path / "sample.pdf")


@pytest.fixture()
def sample_doc(workspace: Layout, sample_pdf: Path):
    manifest = parse_pdf(sample_pdf, workspace.ingest_root, "pypdf", layout=workspace,
                         doc_id="sample-la", title="Sample Linear Algebra")
    for ch in manifest["chapters"]:
        chunk_and_anchor("sample-la", ch["chapter_id"], layout=workspace)
    return workspace, "sample-la"


@pytest.fixture()
def store(sample_doc) -> DocStore:
    layout, doc_id = sample_doc
    return DocStore(layout, doc_id)
