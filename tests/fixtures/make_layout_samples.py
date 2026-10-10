"""11.0 技术验证样本生成器：普通/双栏/旋转/公式密集四类版式（确定性）。"""

from __future__ import annotations

from pathlib import Path


def _build_pdf(pages: list[dict], path: Path) -> Path:
    """pages: [{lines:[(text,size,x,y)], rotate:0|90|180|270}]"""
    objs: dict[int, bytes] = {}
    counter = {"n": 1}

    def alloc() -> int:
        num = counter["n"]
        counter["n"] += 1
        return num

    catalog_id = alloc()
    pages_id = alloc()
    font_id = alloc()
    content_ids = [alloc() for _ in pages]
    page_ids = [alloc() for _ in pages]
    objs[font_id] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"

    for idx, spec in enumerate(pages):
        ops = []
        for text, size, x, y in spec["lines"]:
            safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            ops.append(f"BT /F1 {size} Tf {x} {y} Td ({safe}) Tj ET")
        stream = "\n".join(ops).encode("latin-1")
        objs[content_ids[idx]] = (f"<< /Length {len(stream)} >>\nstream\n".encode("latin-1")
                                  + stream + b"\nendstream")
        rotate = int(spec.get("rotate", 0)) % 360
        rot = f" /Rotate {rotate}" if rotate else ""
        objs[page_ids[idx]] = (
            f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 612 792]{rot} "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> "
            f"/Contents {content_ids[idx]} 0 R >>").encode("latin-1")

    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objs[pages_id] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode("latin-1")
    objs[catalog_id] = f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode("latin-1")

    out = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for num in sorted(objs):
        offsets[num] = len(out)
        out += f"{num} 0 obj\n".encode() + objs[num] + b"\nendobj\n"
    xref_pos = len(out)
    max_num = max(objs) + 1
    out += f"xref\n0 {max_num}\n".encode() + b"0000000000 65535 f \n"
    for num in range(1, max_num):
        out += (f"{offsets[num]:010d} 00000 n \n".encode() if num in offsets
                else b"0000000000 65535 f \n")
    out += (f"trailer\n<< /Size {max_num} /Root {catalog_id} 0 R >>\n"
            f"startxref\n{xref_pos}\n%%EOF\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(out))
    return path


def build_plain(path: Path) -> Path:
    return _build_pdf([{
        "lines": [
            ("Chapter 1 Basic Layout", 18, 72, 720),
            ("A paragraph explains the determinant of a matrix.", 11, 72, 680),
            ("Definition: the determinant is a scalar value of a square matrix.", 11, 72, 650),
            ("Theorem: a matrix is invertible iff its determinant is non zero.", 11, 72, 620),
        ],
    }], path)


def build_two_column(path: Path) -> Path:
    lines = []
    for i in range(8):
        y = 720 - i * 30
        lines.append((f"L{i + 1} left column text about matrices and vectors.", 10, 60, y))
        lines.append((f"R{i + 1} right column text continues the discussion.", 10, 330, y))
    return _build_pdf([{"lines": lines}], path)


def build_rotated(path: Path) -> Path:
    return _build_pdf([
        {"rotate": 90, "lines": [
            ("Rotated page sample", 16, 72, 700),
            ("Text on a page whose /Rotate is 90 degrees.", 11, 72, 660),
            ("Definition: rotation must not be applied twice.", 11, 72, 630),
        ]},
        {"rotate": 270, "lines": [
            ("Second rotated page", 16, 72, 700),
            ("Coordinates must round-trip within half a unit.", 11, 72, 660),
        ]},
    ], path)


def build_formula_dense(path: Path) -> Path:
    lines = [("Chapter 2 Formula Density", 18, 72, 720)]
    formulas = [
        "det A = sum over permutations sign(p) product a i p(i)",
        "trace of A equals sum of eigenvalues lambda i",
        "integral from zero to one of f(x) dx equals F(1) minus F(0)",
        "partial derivative of u with respect to t equals laplacian u",
        "sum from i equals one to n of i squared equals n times n plus one",
    ]
    for i, text in enumerate(formulas):
        lines.append((text, 12, 72, 660 - i * 40))
    return _build_pdf([{"lines": lines}], path)


def build_all(out_dir: Path) -> dict[str, Path]:
    return {
        "plain": build_plain(out_dir / "layout-plain.pdf"),
        "two_column": build_two_column(out_dir / "layout-two-column.pdf"),
        "rotated": build_rotated(out_dir / "layout-rotated.pdf"),
        "formula_dense": build_formula_dense(out_dir / "layout-formula.pdf"),
    }


if __name__ == "__main__":
    out = Path(__file__).resolve().parents[1] / "golden"
    for name, p in build_all(out).items():
        print(name, p, p.stat().st_size)
