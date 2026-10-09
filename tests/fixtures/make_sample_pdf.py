"""确定性样本 PDF 生成器（golden 测试用；3 页 ASCII 文本，含标题层级与公式行）。"""

from __future__ import annotations

from pathlib import Path

PAGES: list[list[tuple[str, float, float, float]]] = [
    [
        ("Chapter 1 Determinants", 18, 72, 720),
        ("The determinant of a matrix is a scalar value computed from its elements.", 11, 72, 680),
        ("Definition: the determinant of an n by n matrix A is defined by the Leibniz formula.", 11, 72, 650),
        ("det A = sum over permutations sign(p) times product of a i p(i)", 12, 72, 610),
        ("Motivation: determinants tell us when a system of linear equations has a unique solution.", 11, 72, 570),
    ],
    [
        ("1.1 Geometric Meaning", 14, 72, 720),
        ("The absolute value of the determinant equals the volume of the parallelepiped.", 11, 72, 680),
        ("Theorem: a square matrix is invertible if and only if its determinant is non zero.", 11, 72, 650),
        ("Proof: if the determinant is non zero the reduced row echelon form is the identity.", 11, 72, 620),
        ("Figure 1-1 shows the area interpretation for two by two matrices.", 11, 72, 580),
    ],
    [
        ("Chapter 2 Matrices", 18, 72, 720),
        ("A matrix is a rectangular array of numbers arranged in rows and columns.", 11, 72, 680),
        ("Definition: the identity matrix has ones on the diagonal and zeros elsewhere.", 11, 72, 650),
        ("Theorem: the product of two invertible matrices is invertible.", 11, 72, 620),
    ],
]


def build_sample_pdf(path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    objs: dict[int, bytes] = {}
    counter = {"n": 1}

    def alloc() -> int:
        num = counter["n"]
        counter["n"] += 1
        return num

    catalog_id = alloc()
    pages_id = alloc()
    font_id = alloc()
    content_ids = [alloc() for _ in PAGES]
    page_ids = [alloc() for _ in PAGES]
    outline_titles = ("Chapter 1 Determinants", "Chapter 2 Matrices")
    outlines_id = alloc()
    outline_ids = [alloc() for _ in outline_titles]

    objs[font_id] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"

    for idx, lines in enumerate(PAGES):
        ops = []
        for text, size, x, y in lines:
            safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            ops.append(f"BT /F1 {size} Tf {x} {y} Td ({safe}) Tj ET")
        stream = "\n".join(ops).encode("latin-1")
        objs[content_ids[idx]] = (f"<< /Length {len(stream)} >>\nstream\n".encode("latin-1")
                                  + stream + b"\nendstream")

    for idx, pid in enumerate(page_ids):
        objs[pid] = (
            f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> "
            f"/Contents {content_ids[idx]} 0 R >>").encode("latin-1")

    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objs[pages_id] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode("latin-1")

    targets = (page_ids[0], page_ids[2])
    for i, title in enumerate(outline_titles):
        objs[outline_ids[i]] = (
            f"<< /Title ({title}) /Parent {outlines_id} 0 R "
            f"/Dest [{targets[i]} 0 R /XYZ null null null] >>").encode("latin-1")
    outline_kids = " ".join(f"{n} 0 R" for n in outline_ids)
    objs[outlines_id] = (f"<< /Type /Outlines /Kids [{outline_kids}] "
                         f"/Count {len(outline_ids)} >>").encode("latin-1")
    objs[catalog_id] = (f"<< /Type /Catalog /Pages {pages_id} 0 R "
                        f"/Outlines {outlines_id} 0 R >>").encode("latin-1")

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
    path.write_bytes(bytes(out))
    return path


if __name__ == "__main__":
    p = build_sample_pdf(Path(__file__).resolve().parents[1] / "golden" / "sample.pdf")
    print("wrote", p, p.stat().st_size)
