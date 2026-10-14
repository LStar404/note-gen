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
from pathlib import Path

# 第三方库（torch/transformers 等）可能向 stdout 打印本地编码日志；强制 UTF-8 并尽量保持安静
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _read_request() -> dict:
    if len(sys.argv) < 2:
        return {"mode": "probe"}
    mode = sys.argv[1]
    raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    req = json.loads(raw) if raw.strip() else {}
    req["mode"] = mode
    return req


def _write(obj: dict, req: dict | None = None) -> None:
    """结果优先写入 output_path（UTF-8），避免 stdout 被第三方日志污染导致解析失败。

    落盘失败不得吞结果（实测：低完整性解释器写不了中完整性临时目录、杀软瞬时锁
    都会 PermissionError）：退回 stdout 契约，父进程 _decode_worker_stdout 兜底解析。"""
    payload = json.dumps(obj, ensure_ascii=False)
    out_path = (req or {}).get("output_path")
    if out_path:
        try:
            Path(out_path).write_text(payload, encoding="utf-8")
            sys.stdout.write("RESULT_OK")
            sys.stdout.flush()
            return
        except OSError:
            pass
    sys.stdout.write(payload)
    sys.stdout.flush()


# --------------------------------------------------------------------------- 公共
def _pages_meta_from_pdf(pdf_path: str) -> list[dict]:
    """页面几何元数据：pypdf 优先，worker 环境无 pypdf 时用 pymupdf 兜底。

    两个库的 y 轴约定相反（pypdf y 向上 / pymupdf y 向下），兜底时按
    mediabox 高度翻转换算到 pypdf 约定，保证 view_box 语义一致。
    """
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
        pass
    try:
        import pymupdf  # noqa: F401  （fitz 同名包兼容）

        try:
            import pymupdf as fitz
        except Exception:
            import fitz
        out = []
        with fitz.open(pdf_path) as doc:
            for page in doc:
                mb = page.mediabox
                flip = float(mb.y0) + float(mb.y1)   # y 向下 → 向上的换算和
                cb = page.cropbox or mb
                view = [float(cb.x0), flip - float(cb.y1), float(cb.x1), flip - float(cb.y0)]
                out.append({"view_box": view,
                            "user_unit": float(getattr(page, "user_unit", 1.0) or 1.0),
                            "rotation": int(page.rotation or 0) % 360})
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
    """可用性探测；req["only"] 只探指定模块（全量探测要 import torch 等重型库，
    实测 8.6s/次——适配器构造只需自己的模块版本）。"""
    out = {"ok": True, "python": sys.version.split()[0], "modules": {}}
    names = req.get("only") or ["marker", "docling", "pix2text", "torch",
                                "onnxruntime", "pypdf", "pymupdf"]
    for name in names:
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

    clamp_cnt = [0]
    drop_cnt = [0]

    def emit(page_no, btype, level, text, bb, height):
        if page_no is None or not text:
            return
        idx = int(page_no) - 1
        if idx < 0 or idx >= len(pages):
            return
        meta = pages_meta[idx]
        vb = meta["view_box"]
        x0, y0, x1, y1 = bb
        # docling bbox：页面顶左原点、y 向下的页面单位——单位未必是点（实测混过像素：
        # y=1568 在 880pt 页上越界，一个坏块曾毁掉 230/364 页的整本书导入）。
        # 按 docling 自报页高与 view_box 页高等比映射（单位由页高关系确定，非猜测），
        # 最后夹进 view_box 兜底；整块越界的丢弃并计数——单块问题不得升级成整书失败。
        src_h = float(height or 0) or (float(vb[3]) - float(vb[1]))
        dst_h = (float(vb[3]) - float(vb[1])) or 1.0
        s = dst_h / src_h
        fy0 = src_h - max(y0, y1)
        fy1 = src_h - min(y0, y1)
        pdf_bbox = [float(vb[0]) + min(x0, x1) * s, float(vb[1]) + fy0 * s,
                    float(vb[0]) + max(x0, x1) * s, float(vb[1]) + fy1 * s]
        clamped = [min(max(pdf_bbox[0], float(vb[0])), float(vb[2])),
                   min(max(pdf_bbox[1], float(vb[1])), float(vb[3])),
                   min(max(pdf_bbox[2], float(vb[0])), float(vb[2])),
                   min(max(pdf_bbox[3], float(vb[1])), float(vb[3]))]
        if clamped[0] >= clamped[2] or clamped[1] >= clamped[3]:
            drop_cnt[0] += 1
            return
        if clamped != pdf_bbox:
            clamp_cnt[0] += 1
        pages[idx]["blocks"].append({"type": btype, "level": level, "text": text.strip(),
                                     "bbox": clamped})

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
    if clamp_cnt[0]:
        warnings.append(f"{clamp_cnt[0]} 个块坐标越界，已夹到页面范围")
    if drop_cnt[0]:
        warnings.append(f"{drop_cnt[0]} 个块整块越界，已丢弃（不猜位置）")

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


# --------------------------------------------------------------------------- OCR（纯图片页）
def ocr_pages(req: dict) -> dict:
    """纯图片/扫描页 OCR（5.1.1 OCR 分支）：渲染页图 -> rapidocr 识别 -> 版面行块。

    bbox 用图像尺寸与页面 view_box 自标定换算到 PDF 用户空间（顶左像素 -> 底左点）。
    """
    try:
        import pypdfium2 as pdfium
        from rapidocr import RapidOCR
    except Exception as exc:
        return {"ok": False, "parser": "ocr", "error": f"OCR 依赖不可用: {exc}"}

    pdf_path = req["pdf_path"]
    pages_meta = req.get("pages_meta") or _pages_meta_from_pdf(pdf_path)
    targets = [int(p) for p in req.get("pages", []) if int(p) - 1 < len(pages_meta)]
    dpi = float(req.get("dpi", 150))
    scale = dpi / 72.0
    render_dir = req.get("render_dir")   # 可选：顺手存页面 PNG（视觉补 OCR 用，本就渲染了）

    try:
        engine = RapidOCR()
    except Exception as exc:
        return {"ok": False, "parser": "ocr", "error": f"rapidocr 初始化失败: {exc}"}

    doc = pdfium.PdfDocument(pdf_path)
    out_pages = []
    warnings: list[str] = []
    for page_no in targets:
        meta = pages_meta[page_no - 1]
        vb = meta["view_box"]
        entry = {"page": page_no, "status": "ok", "blocks": []}
        try:
            page = doc[page_no - 1]
            bitmap = page.render(scale=scale)
            image = bitmap.to_pil()
            if render_dir:
                # 必须存 PIL 副本：bitmap.save() 会破坏 pdfium 位图，
                # 随后的 OCR 拿到空图（实测 0 块 vs 正常 47 块——曾让整本书全变空页）
                Path(render_dir).mkdir(parents=True, exist_ok=True)
                image.save(str(Path(render_dir) / f"page_{page_no:04d}.png"))
            width_px, height_px = image.size
            result = engine(image)
            txts = getattr(result, "txts", None)
            boxes = getattr(result, "boxes", None)
            if txts is None and isinstance(result, (list, tuple)) and result:
                first = result[0]
                boxes = getattr(first, "boxes", None) or (first[0] if isinstance(first, (list, tuple)) else None)
                txts = getattr(first, "txts", None) or (first[1] if isinstance(first, (list, tuple)) else None)
            if not txts:
                entry["status"] = "blank"
                out_pages.append(entry)
                continue
            sx = (vb[2] - vb[0]) / max(1.0, float(width_px))
            sy = (vb[3] - vb[1]) / max(1.0, float(height_px))
            for text, box in zip(txts, boxes):
                text = str(text or "").strip()
                if not text:
                    continue
                xs = [float(pt[0]) for pt in box]
                ys = [float(pt[1]) for pt in box]
                pdf_bbox = [vb[0] + min(xs) * sx, vb[3] - max(ys) * sy,
                            vb[0] + max(xs) * sx, vb[3] - min(ys) * sy]
                entry["blocks"].append({"type": "paragraph", "level": 0, "text": text,
                                        "bbox": pdf_bbox, "confidence": None})
            if not entry["blocks"]:
                entry["status"] = "blank"
        except Exception as exc:
            entry["status"] = "failed"
            entry["error"] = str(exc)[:200]
            warnings.append(f"page {page_no}: {exc}")
        out_pages.append(entry)

    return {
        "ok": True,
        "parser": "ocr",
        "version": "rapidocr",
        "pages": out_pages,
        "coordinate": {
            "source_space": "ocr_pixels_topleft",
            "source_to_pdf": [1.0, 0.0, 0.0, 1.0, 0.0, 0.0],
            "view_box": pages_meta[0]["view_box"] if pages_meta else [0, 0, 612, 792],
            "user_unit": 1.0,
            "rotation": 0,
            "derivation": "OCR 行框（渲染像素，顶左原点）按图像尺寸与页面 view_box 自标定换算",
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


# --------------------------------------------------------------------------- pymupdf
def _decode_spans(spans: list[dict]) -> str:
    """行内 span 文本拼接 + MathType MT2 解码。

    worker 以脚本方式运行（python src/ingest/parser_workers.py）时 src 包不在
    sys.path，需显式引导仓库根；解码失败宁可抛错也不静默退回乱码。
    """
    root = str(Path(__file__).resolve().parents[2])
    if root not in sys.path:
        sys.path.insert(0, root)
    from src.core.text import repair_surrogates
    from src.ingest.mathtype_decode import combine_negations, decode_mt2_text
    parts = [
        decode_mt2_text(str(sp.get("font") or ""), sp.get("text", ""), combine=False)
        for sp in spans
    ]
    return combine_negations(repair_surrogates("".join(parts)))


def _fitz_line_type(text: str, size: float, body: float) -> tuple[str, int]:
    """行级类型启发式：大字号→标题；数学符号密集且无中文→公式；其余段落。"""
    s = text.strip()
    if size >= 1.25 * body and len(s) <= 80:
        return "heading", 2
    cjk = any("\u4e00" <= c <= "\u9fff" for c in s)
    letters = sum(1 for c in s if c.isascii() and c.isalpha())
    mathy = sum(1 for c in s if c in "=+-*/^_()[]{}<>|")
    if mathy >= 2 and not cjk and letters <= 3:
        return "formula", 0
    return "paragraph", 0


def pymupdf_extract(req: dict) -> dict:
    """PyMuPDF（fitz）文本行抽取：预定义 CMap 中文 PDF（无 ToUnicode）保真，
    速度远快于 marker。bbox 翻转 y 轴到 PDF 用户空间；旋转页不猜（5.1.5），
    标 failed 交由上层降级。"""
    try:
        import fitz  # pymupdf ≥1.24 同时导出 fitz / pymupdf 两个名字
    except Exception as exc:
        return {"ok": False, "parser": "pymupdf", "error": f"pymupdf 不可用: {exc}"}

    pdf_path = req["pdf_path"]
    pages_meta = req.get("pages_meta") or _pages_meta_from_pdf(pdf_path)
    if not pages_meta:
        # 两个库都取不到几何元数据时宁可失败也不静默返回 0 页（曾致整书抽取悄悄落空）
        return {"ok": False, "parser": "pymupdf", "error": "无法读取页面几何元数据（pypdf/pymupdf 均不可用）"}
    warnings: list[str] = []
    pages: list[dict] = []
    try:
        doc = fitz.open(pdf_path)
    except Exception as exc:
        return {"ok": False, "parser": "pymupdf", "error": f"打开 PDF 失败: {exc}"}

    try:
        # page_start：分片并行抽取时的绝对页偏移（父进程按片拼接，页号必须保持全书序）
        page_start = int(req.get("page_start") or 0)
        for idx in range(len(pages_meta)):
            meta = pages_meta[idx]
            vb = meta.get("view_box") or [0, 0, 0, 0]
            page_out = {"page": page_start + idx + 1, "status": "ok", "blocks": []}
            if int(meta.get("rotation", 0) or 0) % 360 != 0:
                page_out["status"] = "failed"
                page_out["error"] = "旋转页不走 pymupdf 快路径（5.1.5 禁止猜旋转）"
                pages.append(page_out)
                continue
            try:
                data = doc[page_start + idx].get_text("dict")
            except Exception as exc:
                page_out["status"] = "failed"
                page_out["error"] = str(exc)[:200]
                pages.append(page_out)
                continue

            lines: list[dict] = []
            for blk in data.get("blocks", []):
                if blk.get("type") != 0:          # 0 = 文本块
                    continue
                for ln in blk.get("lines", []):
                    spans = ln.get("spans", [])
                    # MathType MT2 字体：字形名错位（named two 画 ∈），抽取原样是乱码，
                    # 逐 span 解码后按行合成否定斜杠（跨 span 组合）
                    text = _decode_spans(spans).strip()
                    boxes = [sp.get("bbox") for sp in spans if sp.get("bbox")]
                    if not text or not boxes:
                        continue
                    x0 = min(b[0] for b in boxes)
                    y0 = min(b[1] for b in boxes)
                    x1 = max(b[2] for b in boxes)
                    y1 = max(b[3] for b in boxes)
                    size = max((float(sp.get("size") or 0.0)) for sp in spans) or 10.0
                    lines.append({"text": text, "box": (x0, y0, x1, y1), "size": size})
            if not lines:
                page_out["status"] = "blank"
                pages.append(page_out)
                continue

            sizes = sorted(l["size"] for l in lines)
            body = sizes[len(sizes) // 2] or 10.0
            top, bottom = float(vb[3]), float(vb[1])
            for l in lines:
                x0, y0, x1, y1 = l["box"]
                # fitz 上左原点 → PDF 用户空间（y 轴翻转；仅 rotation=0，5.1.5 禁猜）
                bbox = [x0, bottom + top - y1, x1, bottom + top - y0]
                kind, level = _fitz_line_type(l["text"], l["size"], body)
                page_out["blocks"].append({"type": kind, "level": level,
                                           "text": l["text"], "bbox": bbox})
            pages.append(page_out)
    finally:
        doc.close()

    return {"ok": True, "parser": "pymupdf", "pages": pages, "warnings": warnings,
            "coordinate": {
                "source_space": "pdf_user_space",
                "source_to_pdf": [1.0, 0.0, 0.0, 1.0, 0.0, 0.0],
                "derivation": "pymupdf 文本行 bbox 经 y 轴翻转换算到 PDF 用户空间（rotation=0）"}}


# --------------------------------------------------------------------------- 图像
def extract_images(req: dict) -> dict:
    """内嵌位图提取（图资源刷新）：TeX 书的插图多为内嵌 PNG/JPEG——
    返回每张图的放置矩形（PDF 用户空间，y 轴已翻转）并把像素图存到 figures_dir。
    旋转页不猜（5.1.5）直接跳过。"""
    try:
        import fitz
    except Exception as exc:
        return {"ok": False, "parser": "pymupdf", "error": f"pymupdf 不可用: {exc}"}

    pdf_path = req["pdf_path"]
    pages_meta = req.get("pages_meta") or _pages_meta_from_pdf(pdf_path)
    out_dir = req.get("figures_dir")
    figures: list[dict] = []
    try:
        doc = fitz.open(pdf_path)
    except Exception as exc:
        return {"ok": False, "parser": "pymupdf", "error": f"打开 PDF 失败: {exc}"}

    try:
        for idx in range(len(pages_meta)):
            meta = pages_meta[idx]
            vb = meta.get("view_box") or [0, 0, 0, 0]
            if int(meta.get("rotation", 0) or 0) % 360 != 0:
                continue
            page = doc[idx]
            top, bottom = float(vb[3]), float(vb[1])
            seen: set[int] = set()
            for img in page.get_images(full=True):
                xref = img[0]
                if xref in seen:
                    continue
                seen.add(xref)
                for ri, rect in enumerate(page.get_image_rects(xref) or []):
                    if rect.is_empty or rect.width < 40 or rect.height < 40:
                        continue
                    # 放置矩形常超出页面（多联图/出血排版）：夹紧到页面即可见区域，
                    # 不做「超大=背景图」猜测——实测会把真插图滤掉
                    bbox = [max(rect.x0, float(vb[0])),
                            max(bottom, bottom + top - rect.y1),
                            min(rect.x1, float(vb[2])),
                            min(top, bottom + top - rect.y0)]
                    if bbox[2] - bbox[0] < 40 or bbox[3] - bbox[1] < 40:
                        continue
                    fname = None
                    if out_dir:
                        Path(out_dir).mkdir(parents=True, exist_ok=True)
                        fname = f"img_{idx + 1:04d}_{xref}_{ri}.png"
                        pix = fitz.Pixmap(doc, xref)
                        if pix.n - pix.alpha > 3:          # CMYK → RGB
                            pix = fitz.Pixmap(fitz.csRGB, pix)
                        pix.save(str(Path(out_dir) / fname))
                    figures.append({"page": idx + 1, "bbox": bbox, "file": fname})
        return {"ok": True, "parser": "pymupdf", "figures": figures}
    finally:
        doc.close()


def main() -> int:
    req = _read_request()
    mode = req.get("mode", "probe")
    handlers = {
        "probe": probe,
        "marker": marker_extract,
        "docling": docling_extract,
        "pymupdf": pymupdf_extract,
        "extract_images": extract_images,
        "ocr_pages": ocr_pages,
        "pix2text": pix2text_recognize,
    }
    handler = handlers.get(mode)
    if handler is None:
        _write({"ok": False, "error": f"未知模式: {mode}"}, req)
        return 1
    _write(handler(req), req)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
