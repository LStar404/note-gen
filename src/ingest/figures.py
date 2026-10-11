"""图资源刷新（5.1 补全）：从 PDF 内嵌位图登记 figure 资源并回写解析产物。

TeX 书的插图多为内嵌 PNG/JPEG，而 pypdf/pymupdf 快路径只抽文本——
图资源目录为空，笔记里的 ![{fig_id}] 只能显示破图。本模块：
1. 抽取内嵌图放置矩形（夹紧到页面）→ 稳定 figure id（fig_{页*100+序:08d}，重建不换号）
2. 像素图落盘 data/<doc>/figures/
3. ingest pages JSON 的 figure 版面块整体重写（幂等/自愈：旧刷新残留一并清除）
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from src.core.errors import PipelineError
from src.core.paths import Layout, check_doc_id

from .parse_pdf import run_worker


def _stable_fig_id(page: int, seq: int) -> str:
    return f"fig_{page * 100 + seq:08d}"


def _caption_for(bboxes: list[dict], img_bbox: list[float]) -> str:
    """图注就近取：图下方 45pt 内的行（取首个），否则给占位文字（图锚点需非空文本）。"""
    below = [b for b in bboxes
             if (b.get("bbox") or [0, 0, 0, 0])[3] <= img_bbox[1] + 5
             and (b.get("bbox") or [0, 0, 0, 0])[3] >= img_bbox[1] - 45
             and (b.get("text") or "").strip()]
    if below:
        return (below[0]["text"] or "").strip()[:60]
    return "图"


def refresh_figures(pdf_path: Path, layout: Layout, doc_id: str,
                    *, parser_python: str | None = None) -> list[dict]:
    """抽取内嵌图 → figures/ + pages JSON figure 块整体重写。
    返回 [{figure_id, page, caption}]。幂等：重复刷新结果一致（id 稳定）。"""
    check_doc_id(doc_id)
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise PipelineError(f"PDF 不存在: {pdf_path}")

    import pypdf

    reader = pypdf.PdfReader(str(pdf_path))
    pages_meta = []
    for p in reader.pages:
        def box(b):
            return [float(b.left), float(b.bottom), float(b.right), float(b.top)]

        pages_meta.append({
            "view_box": box(p.cropbox) if p.cropbox is not None else box(p.mediabox),
            "user_unit": float(p.get("/UserUnit", 1.0) or 1.0),
            "rotation": int(p.rotation or 0) % 360,
        })

    staging = Path(tempfile.mkdtemp(prefix="ng-figs-"))
    try:
        out = run_worker("extract_images", {"pdf_path": str(pdf_path),
                                            "pages_meta": pages_meta,
                                            "figures_dir": str(staging)},
                         python=parser_python)
        if not out.get("ok"):
            raise PipelineError(f"图像提取失败: {out.get('error')}")

        fig_dir = layout.figures_dir(doc_id)
        fig_dir.mkdir(parents=True, exist_ok=True)
        pages_dir = layout.ingest_root / doc_id / "pages"

        result: list[dict] = []
        per_page_seq: dict[int, int] = {}
        fig_blocks_by_page: dict[int, list[dict]] = {}
        keep_ids: set[str] = set()
        for f in out.get("figures", []):
            page = int(f["page"])
            seq = per_page_seq.get(page, 0)
            per_page_seq[page] = seq + 1
            if seq > 99:
                continue                        # id 编码为 页*100+序，序号防越页碰撞
            fig_id = _stable_fig_id(page, seq)
            keep_ids.add(fig_id)
            src = staging / f["file"] if f.get("file") else None
            if src and src.exists():
                shutil.move(str(src), str(fig_dir / f"{fig_id}.png"))
            result.append({"figure_id": fig_id, "page": page, "bbox": f["bbox"]})

        # 图注就近取（需要先读各页现有文本块）
        for r in result:
            page_file = pages_dir / f"{r['page']:04d}.json"
            blocks = []
            if page_file.exists():
                try:
                    blocks = json.loads(page_file.read_text(encoding="utf-8")).get("blocks", [])
                except Exception:
                    blocks = []
            caption = _caption_for(blocks, r["bbox"])
            r["caption"] = caption
            fig_blocks_by_page.setdefault(r["page"], []).append({
                "type": "figure", "level": 0, "text": caption or "图",
                "bbox": r["bbox"], "figure_id": r["figure_id"],
                "image_path": f"data/{doc_id}/figures/{r['figure_id']}.png"})

        # figure 块整体重写（幂等/自愈）：旧刷新残留的坏 bbox、错位 id 一并清除
        for page_file in pages_dir.glob("*.json"):
            data = json.loads(page_file.read_text(encoding="utf-8"))
            page_no = int(data.get("page", 0))
            kept = [b for b in data.get("blocks", [])
                    if not (b.get("type") == "figure" and b.get("figure_id"))]
            data["blocks"] = kept + fig_blocks_by_page.get(page_no, [])
            page_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        # 陈旧图文件清理：只留本次生成的 id
        for p in fig_dir.glob("*.png"):
            if p.stem not in keep_ids:
                p.unlink()
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return result
