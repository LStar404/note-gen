"""图形/表格裁剪为 PNG（5.1 第 4 步）；三级降级见 ADR D29。"""

from __future__ import annotations

from pathlib import Path


def crop_figure(pdf_path: str | Path, page: int, bbox: list[float], out_path: str | Path,
                caption: str | None = None) -> Path | None:
    """按 bbox 裁剪 300 DPI PNG；无法渲染时写占位图并由调用方记入报告。"""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    x0, y0, x1, y1 = bbox
    width_pt = float(x1 - x0)
    height_pt = float(y1 - y0)

    # 1) pymupdf
    try:
        import fitz  # type: ignore

        doc = fitz.open(str(pdf_path))
        pg = doc[page - 1]
        rect = fitz.Rect(x0, y0, x1, y1)
        pix = pg.get_pixmap(matrix=fitz.Matrix(300 / 72, 300 / 72), clip=rect)
        pix.save(str(out_path))
        doc.close()
        return out_path
    except Exception:
        pass

    # 2) pdfplumber（pypdfium2 渲染）
    try:
        import pdfplumber  # type: ignore

        with pdfplumber.open(str(pdf_path)) as pdf:
            pg = pdf.pages[page - 1]
            im = pg.crop((x0, pg.height - y1, x1, pg.height - y0)).to_image(resolution=300)
            im.save(str(out_path))
        return out_path
    except Exception:
        pass

    # 3) PIL 占位图（标注"占位"，报告可审计）
    try:
        from PIL import Image, ImageDraw

        w = max(8, int(width_pt * 300 / 72))
        h = max(8, int(height_pt * 300 / 72))
        img = Image.new("RGB", (min(w, 4000), min(h, 4000)), (245, 245, 245))
        draw = ImageDraw.Draw(img)
        draw.rectangle([0, 0, img.width - 1, img.height - 1], outline=(160, 160, 160))
        text = (caption or "figure placeholder")[:60]
        draw.text((8, 8), text, fill=(90, 90, 90))
        img.save(str(out_path))
        return out_path
    except Exception:
        return None
