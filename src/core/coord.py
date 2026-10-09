"""坐标系与 bbox 规范（4.3 / 附录 D）：存储为未旋转的原始 PDF 用户空间两点式坐标。"""

from __future__ import annotations

import math

from .errors import SchemaViolation

Matrix = tuple[float, float, float, float, float, float]
Rect = tuple[float, float, float, float]


def transform_rect(rect, matrix) -> list[float]:
    """四角经 [a,b,c,d,e,f]（x'=ax+cy+e, y'=bx+dy+f）变换后取 min/max（附录 D.1）。"""
    x0, y0, x1, y1 = rect
    a, b, c, d, e, f = matrix
    points = [(a * x + c * y + e, b * x + d * y + f)
              for x, y in ((x0, y0), (x0, y1), (x1, y0), (x1, y1))]
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return [min(xs), min(ys), max(xs), max(ys)]


def invert_matrix(m: Matrix) -> Matrix:
    """2x3 仿射矩阵求逆；奇异时报错（不得猜 scale/rotation）。"""
    a, b, c, d, e, f = m
    det = a * d - b * c
    if abs(det) < 1e-12:
        raise SchemaViolation("source_to_pdf 矩阵奇异，无法求逆", code="COORD_TRANSFORM")
    ia, ib = d / det, -b / det
    ic, id_ = -c / det, a / det
    ie = -(ia * e + ic * f)
    if_ = -(ib * e + id_ * f)
    return (ia, ib, ic, id_, ie, if_)


def apply_point(m: Matrix, x: float, y: float) -> tuple[float, float]:
    a, b, c, d, e, f = m
    return (a * x + c * y + e, b * x + d * y + f)


def pixels_top_left_to_pdf(*, scale: float, cropbox: list[float]) -> Matrix:
    """解析器输出"未旋转、左上原点、CropBox 局部像素"时的 source_to_pdf（附录 D.1 特例）。"""
    if scale <= 0:
        raise SchemaViolation("scale 必须为正", code="COORD_TRANSFORM")
    cx0, cy0, cx1, cy1 = cropbox
    return (1.0 / scale, 0.0, 0.0, -1.0 / scale, cx0, cy1)


def validate_bbox(bbox, view_box, *, tol: float = 0.5) -> list[float]:
    """两点式合法：有限数值、x0<x1、y0<y1，落在该页 view_box 内；允许负坐标（4.3/4.7）。"""
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        raise SchemaViolation(f"bbox 必须为 [x0,y0,x1,y1]: {bbox!r}", code="BBOX_INVALID")
    x0, y0, x1, y1 = (float(v) for v in bbox)
    if not all(math.isfinite(v) for v in (x0, y0, x1, y1)):
        raise SchemaViolation("bbox 含非有限数值", code="BBOX_INVALID")
    if x0 >= x1 or y0 >= y1:
        raise SchemaViolation(f"bbox 顺序非法: {bbox!r}", code="BBOX_INVALID")
    vx0, vy0, vx1, vy1 = (float(v) for v in view_box)
    out = [x0, y0, x1, y1]
    if x0 < vx0 - tol or y0 < vy0 - tol or x1 > vx1 + tol or y1 > vy1 + tol:
        raise SchemaViolation(f"bbox 越出 view_box {view_box}: {bbox!r}", code="BBOX_INVALID")
    # 允许 <=0.5 原始单位误差并夹到边界
    out[0] = min(max(out[0], vx0), vx1)
    out[1] = min(max(out[1], vy0), vy1)
    out[2] = min(max(out[2], vx0), vx1)
    out[3] = min(max(out[3], vy0), vy1)
    return out


def rect_union(rects: list[Rect]) -> Rect | None:
    if not rects:
        return None
    xs0 = min(r[0] for r in rects)
    ys0 = min(r[1] for r in rects)
    xs1 = max(r[2] for r in rects)
    ys1 = max(r[3] for r in rects)
    return (xs0, ys0, xs1, ys1)


def rect_area(r) -> float:
    return max(0.0, r[2] - r[0]) * max(0.0, r[3] - r[1])


def rect_inter(a, b) -> Rect | None:
    x0 = max(a[0], b[0])
    y0 = max(a[1], b[1])
    x1 = min(a[2], b[2])
    y1 = min(a[3], b[3])
    if x0 >= x1 or y0 >= y1:
        return None
    return (x0, y0, x1, y1)


def rect_iou(a, b) -> float:
    inter = rect_inter(a, b)
    if inter is None:
        return 0.0
    ia = rect_area(inter)
    ua = rect_area(a) + rect_area(b) - ia
    return ia / ua if ua > 0 else 0.0


def match_scores(selection: Rect, anchor: Rect) -> dict:
    """6.3.2 三条件：cover_anchor / cover_selection / IoU。"""
    inter = rect_inter(selection, anchor)
    ia = rect_area(inter) if inter else 0.0
    sa = rect_area(selection)
    aa = rect_area(anchor)
    return {
        "cover_anchor": (ia / aa) if aa > 0 else 0.0,
        "cover_selection": (ia / sa) if sa > 0 else 0.0,
        "iou": (ia / (sa + aa - ia)) if (sa + aa - ia) > 0 else 0.0,
    }
