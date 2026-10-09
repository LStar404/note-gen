"""存储布局与 ID 白名单（规范 4.2.1 / 4.8 / 10.6.2）。"""

from __future__ import annotations

import re
from pathlib import Path

from .errors import InputError

DOC_ID_RE = re.compile(r"^[a-z0-9-]{4,64}$")
CHAPTER_ID_RE = re.compile(r"^ch[0-9]{2,4}$")
BLOCK_ID_RE = re.compile(r"^b_[A-Za-z0-9]{12}$")
ANCHOR_ID_RE = re.compile(r"^a_[0-9a-f]{8}_[0-9a-f]{16}(-[0-9]+)?$")
FIGURE_ID_RE = re.compile(r"^fig_[0-9]{8}$")
REF_ID_RE = re.compile(r"^ref_[0-9]{4}$")
REV_ID_RE = re.compile(r"^b_[A-Za-z0-9]{12}@r[0-9]+$")
PLAN_ID_RE = re.compile(r"^p_[A-Za-z0-9]{8,32}$")


def check_doc_id(value: str) -> str:
    if not DOC_ID_RE.match(value or ""):
        raise InputError(f"非法 doc_id: {value!r}", code="BAD_DOC_ID")
    return value


def check_chapter_id(value: str) -> str:
    if not CHAPTER_ID_RE.match(value or ""):
        raise InputError(f"非法 chapter_id: {value!r}", code="BAD_CHAPTER_ID")
    return value


def check_block_id(value: str) -> str:
    if not BLOCK_ID_RE.match(value or ""):
        raise InputError(f"非法 block_id: {value!r}", code="BAD_BLOCK_ID")
    return value


def check_anchor_id(value: str) -> str:
    if not ANCHOR_ID_RE.match(value or ""):
        raise InputError(f"非法 anchor_id: {value!r}", code="BAD_ANCHOR_ID")
    return value


def check_figure_id(value: str) -> str:
    if not FIGURE_ID_RE.match(value or ""):
        raise InputError(f"非法 figure_id: {value!r}", code="BAD_FIGURE_ID")
    return value


class Layout:
    """项目内全部固定路径；任何代码不得假设其它路径（规范 4.8）。"""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    # ---- 全局 ----
    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def runtime_db(self) -> Path:
        return self.data_dir / "runtime.db"

    @property
    def locks_dir(self) -> Path:
        return self.data_dir / ".locks"

    @property
    def textbooks_dir(self) -> Path:
        return self.data_dir / "textbooks"

    @property
    def models_registry(self) -> Path:
        return self.data_dir / "models.json"

    @property
    def ingest_root(self) -> Path:
        return self.root / "ingest_out"

    @property
    def cache_dir(self) -> Path:
        return self.root / "cache" / "gen"

    @property
    def reports_dir(self) -> Path:
        return self.root / "reports"

    @property
    def notes_dir(self) -> Path:
        return self.root / "notes_out"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    # ---- 每 doc ----
    def doc_dir(self, doc_id: str) -> Path:
        return self.data_dir / check_doc_id(doc_id)

    def manifest(self, doc_id: str) -> Path:
        return self.doc_dir(doc_id) / "manifest.json"

    def anchors_dir(self, doc_id: str) -> Path:
        return self.doc_dir(doc_id) / "anchors"

    def anchors(self, doc_id: str, chapter_id: str) -> Path:
        return self.anchors_dir(doc_id) / (check_chapter_id(chapter_id) + ".jsonl")

    def blocks_dir(self, doc_id: str) -> Path:
        return self.doc_dir(doc_id) / "blocks"

    def blocks(self, doc_id: str, chapter_id: str) -> Path:
        return self.blocks_dir(doc_id) / (check_chapter_id(chapter_id) + ".jsonl")

    def history_dir(self, doc_id: str) -> Path:
        return self.doc_dir(doc_id) / "history"

    def history(self, doc_id: str, block_id: str) -> Path:
        return self.history_dir(doc_id) / (check_block_id(block_id) + ".jsonl")

    def figures_dir(self, doc_id: str) -> Path:
        return self.doc_dir(doc_id) / "figures"

    def figure_index(self, doc_id: str) -> Path:
        return self.figures_dir(doc_id) / "index.jsonl"

    def figure_png(self, doc_id: str, figure_id: str) -> Path:
        return self.figures_dir(doc_id) / (check_figure_id(figure_id) + ".png")

    def refs_index(self, doc_id: str) -> Path:
        return self.doc_dir(doc_id) / "refs" / "index.jsonl"

    def index_db(self, doc_id: str) -> Path:
        return self.doc_dir(doc_id) / "index.db"

    def transactions_dir(self, doc_id: str) -> Path:
        return self.doc_dir(doc_id) / "transactions"

    def lock_file(self, doc_id: str) -> Path:
        return self.locks_dir / (check_doc_id(doc_id) + ".lock")

    def ingest_pages(self, doc_id: str) -> Path:
        return self.ingest_root / check_doc_id(doc_id) / "pages"

    def cache_file(self, doc_id: str, chapter_id: str) -> Path:
        return self.cache_dir / check_doc_id(doc_id) / (check_chapter_id(chapter_id) + ".json")

    def reports_doc_dir(self, doc_id: str) -> Path:
        return self.reports_dir / check_doc_id(doc_id)

    def notes_doc_dir(self, doc_id: str, out_dir: str | Path | None = None) -> Path:
        base = Path(out_dir) if out_dir else self.notes_dir
        return base / check_doc_id(doc_id)

    # ---- 安全 ----
    def safe_join(self, base: Path, *parts: str) -> Path:
        """joinpath 后校验仍在 base 内（10.6.2 路径遍历防护）。"""
        target = base.joinpath(*parts).resolve()
        base_r = base.resolve()
        if target != base_r and base_r not in target.parents:
            raise InputError(f"路径越界: {target}", code="PATH_TRAVERSAL")
        return target

    def ensure_base(self) -> "Layout":
        for p in (
            self.data_dir,
            self.locks_dir,
            self.textbooks_dir,
            self.ingest_root,
            self.cache_dir,
            self.reports_dir,
            self.notes_dir,
            self.logs_dir,
        ):
            p.mkdir(parents=True, exist_ok=True)
        return self


def rel_to_root(root: str | Path, path: str | Path) -> str:
    """返回相对项目根的 POSIX 风格路径（写入 manifest/figure.path 用）。"""
    return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
