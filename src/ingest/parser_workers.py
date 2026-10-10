"""解析器 worker（ADR D31）：在独立解释器中运行重型解析器，JSON over stdio。

用法：<python> src/ingest/parser_workers.py <marker|docling|pix2text|probe>
stdin : JSON 请求；stdout: JSON 响应。

响应契约（供 src/ingest/parse_pdf.py 消费）：
{
  "ok": true, "parser": "marker", "version": "...",
  "pages": [{"page": 1, "status": "ok|blank|failed",
             "blocks": [{"type":"heading|paragraph|formula|figure|table",
                         "level":0-6,"text":"...","bbox":[x0,y0,x1,y1]}]}],
  "coordinate": {"source_space": "...", "source_to_pdf": [a,b,c,d,e,f],
                 "view_box": [...], "user_unit": 1.0, "rotation": 0, "derivation": "..."},
  "warnings": []
}
bbox 位于 source_space；由 parse_pdf 经 source_to_pdf 变换到 4.3 契约。缺少必要元数据时
该页 status=failed，禁止猜 scale/rotation（5.1.5）。
"""

from __future__ import annotations

import json
import sys


def _read_request() -> dict:
    if len(sys.argv) < 2:
        return {"mode": "probe"}
    mode = sys.argv[1]
    raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    req = json.loads(raw) if raw.strip() else {}
    req["mode"] = mode
    return req


def _write(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False))
    sys.stdout.flush()


# --------------------------------------------------------------------------- 公共
def _pages_meta_from_pdf(pdf_path: str) -> list[dict]:
    try:
        import pypdf

        reader = pypdf.PdfReader(pdf_path)
        out = []
        for p in reader.pages:
            def box(b):
                return [float(b.left), float(b.bottom), float(b.right), float(b.top)]

            view = box(p.cropbox) if p.cropbox is not None else box(p.mediabox)
            out.append({"view_box": view,
                        "user_unit": float(p.get("/UserUnit", 1.0) or 1.0),
                        "rotation": int(p.rotation or 0) % 360})
        return out
    except Exception:
        return []


def _bbox_values(bbox):
    """marker/docling 的 bbox 可能是对象/字典/序列；统一取 [x0,y0,x1,y1]。"""
    if bbox is None:
        return None
    if isinstance(bbox, (list, tuple)):
        vals = [float(v) for v in bbox]
        return vals[:4] if len(vals) >= 4 else None
    for attr in ("bbox", "lbrt", "coordinates"):
        v = getattr(bbox, attr, None)
        if isinstance(v, (list, tuple)) and len(v) >= 4:
            return [float(x) for x in v[:4]]
    d = bbox.model_dump() if hasattr(bbox, "model_dump") else (dict(bbox) if isinstance(bbox, dict) else None)
    if isinstance(d, dict):
        for key in ("bbox", "coordinates", "points", "lbrt"):
            v = d.get(key)
            if isinstance(v, (list, tuple)) and len(v) >= 4:
                return [float(x) for x in v[:4]]
        if all(k in d for k in ("l", "t", "r", "b")):
            return [float(d["l"]), float(d["t"]), float(d["r"]), float(d["b"])]
        if all(k in d for k in ("x0", "y0", "x1", "y1")):
            return [float(d["x0"]), float(d["y0"]), float(d["x1"]), float(d["y1"])]
    return None


_SKIP_TYPES = {"Line", "Span", "Char", "TableCell", "FigureGroup", "PictureGroup",
               "TableGroup", "ListGroup", "Document", "Page"}


def _map_type(label: str) -> tuple[str, int]:
    kind = (label or "").lower()
    if "sectionheader" in kind or "heading" in kind or "title" in kind or "section" in kind:
        return "heading", 2
    if "equation" in kind or "formula" in kind or "chemical" in kind:
        return "formula", 0
    if "figure" in kind or "picture" in kind or "diagram" in kind or "image" in kind:
        return "figure", 0
    if "table" in kind:
        return "table", 0
    return "paragraph", 0


# --------------------------------------------------------------------------- probe
def probe(req: dict) -> dict:
    out = {"ok": True, "python": sys.version.split()[0], "modules": {}}
    for name in ("marker", "docling", "pix2text", "torch", "onnxruntime", "pypdf"):
        try:
            mod = __import__(name)
            out["modules"][name] = getattr(mod, "__version__", "unknown")
        except Exception as exc:
            out["modules"][name] = None
            out.setdefault("errors", {})[name] = str(exc)[:200]
    return out


# --------------------------------------------------------------------------- marker
def _marker_pipeline():
    from marker.converters.pdf import PdfConverter
    from marker.models import create_model_dict

    models = create_model_dict()
    converter = PdfConverter(config={"output_format": "json"}, artifact_dict=models)
    return converter, models


def marker_extract(req: dict) -> dict:
    """Marker 2.x：build_document → 页/块树；bbox 依页面包围盒自标定换算（5.1.5）。"""
    try:
        converter, _models = _marker_pipeline()
    except Exception as exc:
        return {"ok": False, "parser": "marker", "error": f"marker 不可用: {exc}"}

    pdf_path = req["pdf_path"]
    pages_meta = req.get("pages_meta") or _pages_meta_from_pdf(pdf_path)
    try:
        document = converter.build_document(pdf_path)
    except Exception as exc:
        return {"ok": False, "parser": "marker", "error": f"marker 转换失败: {exc}"}

    warnings: list[str] = []
    pages = []
    derivation = "marker 块 bbox 依页面包围盒自标定（scale_x/scale_y）后翻转 y 轴到 PDF 用户空间"

    for idx in range(len(pages_meta)):
        meta = pages_meta[idx]
        page_out = {"page": idx + 1, "status": "ok", "blocks": []}
        try:
            page_obj = document.pages[idx]
        except Exception:
            page_out["status"] = "failed"
            page_out["error"] = "marker 未返回该页"
            pages.append(page_out)
            continue

        def call(obj, name):
            v = getattr(obj, name, None)
            if callable(v):
                try:
                    return v(document)
                except TypeError:
                    return v()
            return v

        raw_blocks = list(call(page_obj, "contained_blocks")
                          or getattr(page_obj, "children", None) or [])
        blocks = [b for b in raw_blocks
                  if str(getattr(getattr(b, "block_type", None), "name", "")
                         or getattr(b, "block_type", "") or type(b).__name__) not in _SKIP_TYPES]
        page_bbox = _bbox_values(getattr(page_obj, "polygon", None)
                                 or getattr(page_obj, "bbox", None))
        if not blocks:
            page_out["status"] = "blank"
            pages.append(page_out)
            continue

        vb = meta["view_box"]
        # 自标定：marker 坐标盒 → PDF 用户空间（左下原点）
        if page_bbox and (page_bbox[2] - page_bbox[0]) > 1.5 and (page_bbox[3] - page_bbox[1]) > 1.5:
            px0, py0, px1, py1 = page_bbox
            scale_x = (vb[2] - vb[0]) / (px1 - px0)
            scale_y = (vb[3] - vb[1]) / (py1 - py0)

            def to_pdf(bb):
                return [vb[0] + (bb[0] - px0) * scale_x,
                        vb[3] - (bb[3] - py0) * scale_y,
                        vb[0] + (bb[2] - px0) * scale_x,
                        vb[3] - (bb[1] - py0) * scale_y]
        else:
            def to_pdf(bb):  # 归一化 0-1（顶部原点）
                return [vb[0] + min(bb[0], bb[2]) * (vb[2] - vb[0]),
                        vb[3] - max(bb[1], bb[3]) * (vb[3] - vb[1]),
                        vb[0] + max(bb[0], bb[2]) * (vb[2] - vb[0]),
                        vb[3] - min(bb[1], bb[3]) * (vb[3] - vb[1])]

        def emit(block):
            label = str(getattr(getattr(block, "block_type", None), "name", "")
                        or getattr(block, "block_type", "") or type(block).__name__)
            if label in _SKIP_TYPES:
                return
            btype, level = _map_type(label)
            if btype == "heading":
                level = int(getattr(block, "heading_level", 0)
                            or getattr(block, "section_level", 0) or 2) or 2
            text = str(call(block, "raw_text") or "").strip()
            poly = getattr(block, "polygon", None)
            bb = _bbox_values(poly if poly is not None else getattr(block, "bbox", None))
            if bb and text and btype in ("heading", "paragraph", "formula", "table"):
                page_out["blocks"].append({"type": btype, "level": level, "text": text,
                                           "bbox": to_pdf(bb)})
            elif bb and btype == "figure":
                page_out["blocks"].append({"type": "figure", "level": 0,
                                           "text": "![fig_placeholder]", "bbox": to_pdf(bb)})

        for b in blocks:
            emit(b)

        if not page_out["blocks"]:
            page_out["status"] = "blank"
        pages.append(page_out)

    return {
        "ok": True,
        "parser": "marker",
        "version": "2.x",
        "pages": pages,
        "coordinate": {
            "source_space": "marker_bbox",
            "source_to_pdf": [1.0, 0.0, 0.0, 1.0, 0.0, 0.0],
            "view_box": pages_meta[0]["view_box"] if pages_meta else [0, 0, 612, 792],
            "user_unit": 1.0,
            "rotation": 0,
            "derivation": derivation,
        },
        "warnings": warnings,
    }


# --------------------------------------------------------------------------- docling
def docling_extract(req: dict) -> dict:
    """Docling：结构保留好、双栏常见；bbox 依页高显式换算到底部原点。"""
    try:
        from docling.document_converter import DocumentConverter
    except Exception as exc:
        return {"ok": False, "parser": "docling", "error": f"docling 不可用: {exc}"}

    pdf_path = req["pdf_path"]
    pages_meta = req.get("pages_meta") or _pages_meta_from_pdf(pdf_path)
    warnings: list[str] = []
    doc = None
    try:
        doc = DocumentConverter().convert(pdf_path).document
    except Exception as exc:
        # docling-parse 原生组件在含非 ASCII 的安装路径下不可用（Windows）；改用 pypdfium2 后端
        warnings.append(f"默认后端失败，改用 pypdfium2 后端：{str(exc)[:160]}")
        try:
            from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
            from docling.datamodel.base_models import InputFormat
            from docling.document_converter import FormatOption
            from docling.pipeline.standard_pdf_pipeline import StandardPdfPipeline

            conv = DocumentConverter(format_options={
                InputFormat.PDF: FormatOption(backend=PyPdfiumDocumentBackend,
                                              pipeline_cls=StandardPdfPipeline)})
            doc = conv.convert(pdf_path).document
        except Exception as exc2:
            return {"ok": False, "parser": "docling",
                    "error": f"docling 转换失败: {exc2}", "warnings": warnings}

    pages = [{"page": i + 1, "status": "ok", "blocks": []} for i in range(len(pages_meta))]

    def emit(page_no, btype, level, text, bb, height):
        if page_no is None or not text:
            return
        idx = int(page_no) - 1
        if idx < 0 or idx >= len(pages):
            return
        meta = pages_meta[idx]
        x0, y0, x1, y1 = bb
        # docling：坐标单位为页面点；coord_origin=TOPLEFT 时 y 自顶向下
        origin = "topleft"
        pdf_bbox = [
            meta["view_box"][0] + min(x0, x1),
            meta["view_box"][1] + (height - max(y0, y1)),
            meta["view_box"][0] + max(x0, x1),
            meta["view_box"][1] + (height - min(y0, y1)),
        ]
        _ = origin
        pages[idx]["blocks"].append({"type": btype, "level": level, "text": text.strip(),
                                     "bbox": pdf_bbox})

    page_heights = {}
    for page_no, page in (getattr(doc, "pages", {}) or {}).items():
        size = getattr(page, "size", None)
        if size is not None:
            page_heights[int(page_no)] = float(getattr(size, "height", 842.0))

    for item, _level in doc.iterate_items():
        label = str(getattr(item, "label", "")).lower()
        text = (getattr(item, "text", "") or "").strip()
        prov = getattr(item, "prov", None) or []
        if not prov:
            continue
        for p in prov:
            bb = _bbox_values(getattr(p, "bbox", None))
            page_no = getattr(p, "page_no", None)
            if bb is None or page_no is None:
                continue
            btype, level = _map_type(label)
            emit(page_no, btype, level, text, bb, page_heights.get(int(page_no), 842.0))

    for page in pages:
        if not page["blocks"]:
            page["status"] = "blank"

    return {
        "ok": True,
        "parser": "docling",
        "version": "2.x",
        "pages": pages,
        "coordinate": {
            "source_space": "docling_pdf_points",
            "source_to_pdf": [1.0, 0.0, 0.0, 1.0, 0.0, 0.0],
            "view_box": pages_meta[0]["view_box"] if pages_meta else [0, 0, 612, 792],
            "user_unit": 1.0,
            "rotation": 0,
            "derivation": "docling bbox(页面点) 按页高翻转 y 轴到 PDF 用户空间",
        },
        "warnings": warnings,
    }


# --------------------------------------------------------------------------- pix2text
def pix2text_recognize(req: dict) -> dict:
    """公式 OCR 兜底（5.1.3）：对裁剪图识别 LaTeX。"""
    try:
        from pix2text import Pix2Text
    except Exception as exc:
        return {"ok": False, "parser": "pix2text", "error": f"pix2text 不可用: {exc}"}
    out = {"ok": True, "parser": "pix2text", "results": []}
    try:
        p2t = Pix2Text()
    except Exception as exc:
        return {"ok": False, "parser": "pix2text", "error": f"pix2text 初始化失败: {exc}"}
    for item in req.get("items", []):
        try:
            res = p2t.recognize(item["image_path"])
            text = res.get("text") if isinstance(res, dict) else str(res)
        except Exception as exc:
            text = None
            out.setdefault("warnings", []).append(f"{item.get('id')}: {exc}")
        out["results"].append({"id": item.get("id"), "latex": text})
    return out


def main() -> int:
    req = _read_request()
    mode = req.get("mode", "probe")
    handlers = {
        "probe": probe,
        "marker": marker_extract,
        "docling": docling_extract,
        "pix2text": pix2text_recognize,
    }
    handler = handlers.get(mode)
    if handler is None:
        _write({"ok": False, "error": f"未知模式: {mode}"})
        return 1
    _write(handler(req))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
