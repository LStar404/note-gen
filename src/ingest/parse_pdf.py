"""阶段 1 Ingest：PDF → 按页结构化版面块（5.1）。

适配器必须明确源单位/原点/旋转与缩放，页 JSON 记录可逆 `coordinate_meta.source_to_pdf`；
bbox 一律换算到 4.3 契约（未旋转的原始 PDF 用户空间）。禁止猜 scale/rotation。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any

from src.core.coord import pixels_top_left_to_pdf, transform_rect, validate_bbox
from src.core.errors import InputError, PipelineError
from src.core.jsonl import atomic_write_bytes, canonical_json, sha256_file
from src.core.models import Manifest, PageMeta, ParserInfo
from src.core.paths import Layout, rel_to_root
from src.core.text import utc_now_iso

from .chapter_detect import detect_chapters
from .figure_crop import crop_figure
from .formula_ocr import FormulaOCR

PAGE_JSON_VERSION = "1"


def _pdf_page_meta(page) -> dict:
    """manifest.pages 的几何元数据直接取自 PDF（4.3）：view_box/user_unit/rotation。"""

    def box(b):
        return [float(b.left), float(b.bottom), float(b.right), float(b.top)]

    view = box(page.cropbox) if page.cropbox is not None else box(page.mediabox)
    return {
        "view_box": view,
        "user_unit": float(page.get("/UserUnit", 1.0) or 1.0),
        "rotation": int(page.rotation or 0) % 360,
    }


class ParserAdapter:
    """解析器适配器基类：产出页内版面块（源空间）与坐标换算矩阵。"""

    name = "base"
    version = "0"

    def parse_page(self, reader, page_index: int) -> dict:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# 内置 pypdf 适配器（离线兜底，ADR D27）
# ---------------------------------------------------------------------------
class PypdfAdapter(ParserAdapter):
    name = "pypdf"

    def __init__(self):
        import pypdf

        self.version = getattr(pypdf, "__version__", "0")
        self._pypdf = pypdf

    def page_meta(self, page) -> dict:
        def box(b):
            return [float(b.left), float(b.bottom), float(b.right), float(b.top)]
        view = box(page.cropbox) if page.cropbox is not None else box(page.mediabox)
        user_unit = float(page.get("/UserUnit", 1.0) or 1.0)
        rotation = int(page.rotation or 0) % 360
        return {"view_box": view, "user_unit": user_unit, "rotation": rotation}

    def parse_page(self, reader, page_index: int) -> dict:
        page = reader.pages[page_index]
        meta = self.page_meta(page)
        spans: list[dict] = []
        state = {"tm": (1.0, 0.0, 0.0, 1.0, 0.0, 0.0), "tl": 0.0}

        def mat_mul(m1, m2):
            a1, b1, c1, d1, e1, f1 = m1
            a2, b2, c2, d2, e2, f2 = m2
            return (a1 * a2 + c1 * b2, b1 * a2 + d1 * b2,
                    a1 * c2 + c1 * d2, b1 * c2 + d1 * d2,
                    a1 * e2 + c1 * f2 + e1, b1 * e2 + d1 * f2 + f1)

        def operand_after(op, operands, cm, tm):
            """自行跟踪文本矩阵：pypdf 的 visitor_text 回传 tm 时有滞后（Td/Tm 之后才更新）。"""
            name = op.decode("latin-1") if isinstance(op, (bytes, bytearray)) else str(op)
            try:
                if name == "BT":
                    state["tm"] = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)   # 文本对象开始即重置
                    state["tl"] = 0.0
                elif name == "Tm" and len(operands) == 6:
                    state["tm"] = tuple(float(x) for x in operands)
                elif name == "Td" and len(operands) == 2:
                    state["tm"] = mat_mul(state["tm"], (1.0, 0.0, 0.0, 1.0,
                                                        float(operands[0]), float(operands[1])))
                elif name == "TD" and len(operands) == 2:
                    state["tl"] = -float(operands[1])
                    state["tm"] = mat_mul(state["tm"], (1.0, 0.0, 0.0, 1.0,
                                                        float(operands[0]), float(operands[1])))
                elif name == "TL" and len(operands) == 1:
                    state["tl"] = float(operands[0])
                elif name == "T*":
                    state["tm"] = mat_mul(state["tm"], (1.0, 0.0, 0.0, 1.0, 0.0, -state["tl"]))
            except Exception:
                pass

        def visitor(text, cm, tm, font_dict, font_size):
            if not text or not text.strip():
                return
            a1, b1, c1, d1, e1, f1 = cm
            a2, b2, c2, d2, e2, f2 = state["tm"]
            x = a1 * e2 + c1 * f2 + e1
            y = b1 * e2 + d1 * f2 + f1
            size = float(font_size or 10.0)
            width = max(size * 0.5, size * 0.5 * len(text))
            spans.append({
                "text": text,
                "bbox": [x, y - size * 0.25, x + width, y + size],
                "font_size": size,
            })

        try:
            page.extract_text(visitor_text=visitor, visitor_operand_after=operand_after)
        except Exception as exc:
            return {"status": "failed", "error": str(exc), "blocks": [], "meta": meta}

        blocks = _spans_to_blocks(spans)
        if not blocks:
            return {"status": "blank", "blocks": [], "meta": meta}
        return {"status": "ok", "blocks": blocks, "meta": meta,
                "source_space": "pdf_user_space", "source_to_pdf": [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]}


def _spans_to_blocks(spans: list[dict]) -> list[dict]:
    """行/段落合并 + 标题启发式（同页同栏、阅读序连续才合并，合并上限 800 字符）。"""
    if not spans:
        return []
    sizes = sorted(s["font_size"] for s in spans)
    median = sizes[len(sizes) // 2] or 10.0
    lines: list[list[dict]] = [[spans[0]]]
    for s in spans[1:]:
        prev = lines[-1][-1]
        gap = prev["bbox"][1] - s["bbox"][3]          # 上一行底 - 本行顶
        same_line = abs((prev["bbox"][1] + prev["bbox"][3]) / 2 - (s["bbox"][1] + s["bbox"][3]) / 2) < max(2.0, prev["font_size"] * 0.4)
        if same_line:
            lines[-1].append(s)
        else:
            lines.append([s])
        _ = gap

    # 适配器只做行级版面块；段落合并是 anchor 阶段的职责（5.2 切分规则）
    blocks: list[dict] = []
    for line in lines:
        text = "".join(sp["text"] for sp in line).strip()
        if not text:
            continue
        bbox = [min(sp["bbox"][0] for sp in line), min(sp["bbox"][1] for sp in line),
                max(sp["bbox"][2] for sp in line), max(sp["bbox"][3] for sp in line)]
        max_size = max(sp["font_size"] for sp in line)
        if len(text) <= 60 and max_size >= median * 1.25:
            blocks.append({"type": "heading", "level": 2, "text": text, "bbox": bbox})
        else:
            blocks.append({"type": "paragraph", "text": text, "bbox": bbox})
    return blocks


# ---------------------------------------------------------------------------
# Marker / Docling 适配器（默认候选，见 5.1 选型表）：经 parser_workers 子进程运行重型依赖
# ---------------------------------------------------------------------------
class WorkerAdapter(ParserAdapter):
    """在独立解释器（默认 .venv-parsers）中运行的解析器适配器（ADR D31）。"""

    def __init__(self, name: str, python: str | None = None):
        self.name = name
        self.python = python
        info = self._call("probe", {})
        modules = info.get("modules", {}) if isinstance(info, dict) else {}
        if not modules.get(self.name):
            raise InputError(
                f"解析器 {self.name} 不可用：请安装（python -m pip install {self.name}-pdf 等）"
                f"或使用 --parser pypdf",
                detail={"probe": modules})
        self.version = str(modules.get(self.name))

    @staticmethod
    def _resolve_python(explicit: str | None) -> str:
        """worker 解释器：显式配置 > .venv-parsers > 当前解释器（ADR D31）。"""
        import sys

        if explicit:
            return explicit
        root = Path(__file__).resolve().parents[2]
        for cand in (root / ".venv-parsers" / "Scripts" / "python.exe",
                     root / ".venv-parsers" / "bin" / "python"):
            if cand.exists():
                return str(cand)
        return sys.executable

    def _call(self, mode: str, payload: dict) -> dict:
        import subprocess

        worker = Path(__file__).resolve().parent / "parser_workers.py"
        python = self._resolve_python(self.python)
        proc = subprocess.run([python, str(worker), mode],
                              input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                              capture_output=True, cwd=str(Path(__file__).resolve().parents[2]),
                              timeout=1800)
        if proc.returncode != 0:
            raise PipelineError(f"{self.name} worker 退出码 {proc.returncode}: "
                                f"{proc.stderr.decode('utf-8', 'ignore')[-500:]}")
        try:
            return json.loads(proc.stdout.decode("utf-8"))
        except Exception as exc:
            raise PipelineError(f"{self.name} worker 输出非法 JSON: {exc}")

    def parse_document(self, pdf_path: str | Path, pages_meta: list[dict] | None = None) -> dict:
        """整书解析：返回 {pages:[...], coordinate:{...}, warnings:[...]}。"""
        out = self._call(self.name, {"pdf_path": str(Path(pdf_path).resolve()),
                                    "pages_meta": pages_meta or []})
        if not out.get("ok"):
            raise PipelineError(f"{self.name} 解析失败: {out.get('error')}")
        return out

    def parse_page(self, reader, page_index: int) -> dict:  # pragma: no cover - 整书路径覆盖
        raise PipelineError(f"{self.name} 走整书解析路径")


class MarkerAdapter(WorkerAdapter):
    def __init__(self, python: str | None = None):
        super().__init__("marker", python=python)


class DoclingAdapter(WorkerAdapter):
    def __init__(self, python: str | None = None):
        super().__init__("docling", python=python)


def get_adapter(name: str, *, python: str | None = None) -> ParserAdapter:
    name = (name or "auto").lower()
    if name == "marker":
        return MarkerAdapter(python=python)
    if name == "docling":
        return DoclingAdapter(python=python)
    if name == "pypdf":
        return PypdfAdapter()
    if name == "auto":
        for factory in (MarkerAdapter, DoclingAdapter):
            try:
                return factory(python=python)
            except (InputError, PipelineError):
                continue
        return PypdfAdapter()
    raise InputError(f"未知解析器: {name}")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def parse_pdf(pdf_path: str | Path, out_dir: str | Path, parser: str = "auto", *,
              layout: Layout | None = None, doc_id: str | None = None,
              profile: str = "textbook", title: str | None = None,
              chapters_override: list | None = None, ocr: bool = False,
              config: dict | None = None) -> dict:
    """解析 PDF → pages/*.json + manifest（5.1 MUST 顺序执行）。返回 manifest dict。"""
    pdf_path = Path(pdf_path).resolve()
    if not pdf_path.exists():
        raise InputError(f"PDF 不存在: {pdf_path}")
    out_dir = Path(out_dir)
    layout = layout or Layout(out_dir.parent if out_dir.name == "ingest_out" else out_dir)
    import pypdf

    reader = pypdf.PdfReader(str(pdf_path))
    n_pages = len(reader.pages)
    if n_pages == 0:
        raise InputError("PDF 没有任何页面")

    parser_python = (config or {}).get("parser", {}).get("python")
    adapter = get_adapter(parser, python=parser_python)
    pages_meta_all = [_pdf_page_meta(reader.pages[i]) for i in range(n_pages)]
    bulk = (adapter.parse_document(pdf_path, pages_meta=pages_meta_all)
            if hasattr(adapter, "parse_document") else None)
    if bulk:
        for warn in bulk.get("warnings", []):
            print(f"[ingest] {adapter.name}: {warn}", file=sys.stderr)
    doc_id = doc_id or ("doc-" + sha256_file(pdf_path)[:12])
    pages_dir = out_dir / doc_id / "pages"
    if pages_dir.exists():
        shutil.rmtree(pages_dir)
    pages_dir.mkdir(parents=True, exist_ok=True)

    ocr_engine = FormulaOCR() if ocr else None
    page_records: list[dict] = []
    failed = 0
    figure_seq = 0

    for i in range(n_pages):
        meta = _pdf_page_meta(reader.pages[i])
        if bulk is not None:
            page_data = (bulk.get("pages") or [])[i] if i < len(bulk.get("pages") or []) else {}
            coord = bulk.get("coordinate", {})
            result = {
                "status": page_data.get("status", "failed"),
                "blocks": page_data.get("blocks", []),
                "meta": meta,
                "source_space": coord.get("source_space", "pdf_user_space"),
                "source_to_pdf": coord.get("source_to_pdf", [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]),
                "coordinate_derivation": coord.get("derivation", ""),
                "error": page_data.get("error"),
            }
        else:
            result = adapter.parse_page(reader, i)
            result.setdefault("meta", meta)
        view_box = meta["view_box"]
        blocks = result.get("blocks", [])
        source_to_pdf = result.get("source_to_pdf", [1.0, 0.0, 0.0, 1.0, 0.0, 0.0])
        source_space = result.get("source_space", "pdf_user_space")

        normalized: list[dict] = []
        for idx, blk in enumerate(blocks):
            bbox = transform_rect(blk.get("bbox", view_box), source_to_pdf) if source_space != "pdf_user_space" else list(blk.get("bbox", view_box))
            try:
                bbox = validate_bbox(bbox, view_box)
            except Exception as exc:
                raise PipelineError(f"第 {i + 1} 页块 {idx} 坐标非法: {exc}")
            item = {
                "type": blk.get("type", "paragraph"),
                "level": int(blk.get("level", 0) or 0),
                "text": blk.get("text", ""),
                "bbox": bbox,
            }
            if blk.get("confidence") is not None:
                item["confidence"] = float(blk["confidence"])
            if ocr_engine and item["type"] == "formula" and not item["text"].strip():
                latex = ocr_engine.recognize(pdf_path, i + 1, bbox)
                if latex:
                    item["text"] = latex
            if item["type"] in ("figure", "table") and blk.get("image_path") is None:
                figure_seq += 1
                fig_id = f"fig_{figure_seq:08d}"
                png = crop_figure(pdf_path, i + 1, bbox, layout.figures_dir(doc_id) / f"{fig_id}.png",
                                  caption=blk.get("caption"))
                item["image_path"] = rel_to_root(layout.root, png) if png else None
                item["figure_id"] = fig_id
            normalized.append(item)

        page_json = {
            "doc_id": doc_id,
            "page": i + 1,
            "page_width": float(view_box[2] - view_box[0]),
            "page_height": float(view_box[3] - view_box[1]),
            "coordinate_meta": {
                "source_space": source_space,
                "source_to_pdf": list(source_to_pdf),
                "view_box": list(view_box),
                "user_unit": float(meta.get("user_unit", 1.0)),
                "rotation": int(meta.get("rotation", 0)),
            },
            "parse_status": result.get("status", "ok"),
            "blocks": normalized,
        }
        atomic_write_bytes(pages_dir / f"{i + 1:04d}.json",
                           (json.dumps(page_json, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        if result.get("status") == "failed":
            failed += 1
        page_records.append({
            "page": i + 1,
            "view_box": list(view_box),
            "user_unit": float(meta.get("user_unit", 1.0)),
            "rotation": int(meta.get("rotation", 0)),
            "parse_status": result.get("status", "ok"),
        })

    if n_pages and failed / n_pages > 0.05:
        raise PipelineError(f"失败页数 {failed}/{n_pages} 超过 5%，请更换解析器")

    chapters = detect_chapters(reader, page_records, doc_id, pages_dir=pages_dir, profile=profile,
                               override=chapters_override or (config or {}).get("chapters_override"))
    now = utc_now_iso()
    manifest = {
        "schema_version": 2,
        "doc_id": doc_id,
        "profile": profile,
        "title": title or (pdf_path.stem),
        "pdf_path": rel_to_root(layout.root, pdf_path),
        "pdf_sha256": sha256_file(pdf_path),
        "parser": {"name": adapter.name, "version": adapter.version,
                   "config_hash": "sha256:" + hashlib.sha256(canonical_json({
                       "name": adapter.name, "version": adapter.version,
                       "profile": profile, "ocr": bool(ocr)}).encode("utf-8")).hexdigest()},
        "ocr": bool(ocr),
        "data_revision": 1,
        "pages": page_records,
        "collision_map": {},
        "chapters": chapters,
        "stage_hashes": {
            "ingest": {
                "input_hash": "sha256:" + hashlib.sha256(
                    (sha256_file(pdf_path) + adapter.name + adapter.version + profile).encode("utf-8")).hexdigest(),
                "output_hash": "sha256:" + _pages_hash(pages_dir),
                "version": PAGE_JSON_VERSION,
            }
        },
        "created_at": now,
        "updated_at": now,
    }
    Manifest.model_validate(manifest)  # 写入前 schema 校验
    # 初版 manifest 直接落盘（此时尚无已提交状态）
    doc_dir = layout.doc_dir(doc_id)
    for sub in ("anchors", "blocks", "history", "figures", "refs"):
        (doc_dir / sub).mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(layout.manifest(doc_id),
                       (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return manifest


def _pages_hash(pages_dir: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(pages_dir.glob("*.json")):
        h.update(p.name.encode("utf-8"))
        h.update(p.read_bytes())
    return h.hexdigest()
