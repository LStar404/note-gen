"""坐标系（4.3/附录 D/E-20）：旋转换算、bbox 校验、选区匹配三条件。"""

from __future__ import annotations

import math

import pytest

from src.core.coord import (apply_point, invert_matrix, match_scores, pixels_top_left_to_pdf,
                            rect_iou, transform_rect, validate_bbox)
from src.core.errors import SchemaViolation


def test_transform_rect_identity():
    assert transform_rect([1, 2, 3, 4], (1, 0, 0, 1, 0, 0)) == [1.0, 2.0, 3.0, 4.0]


def test_transform_rect_pixels_top_left():
    m = pixels_top_left_to_pdf(scale=2.0, cropbox=[0, 0, 595, 842])
    out = transform_rect([0, 0, 100, 200], m)
    assert out == [0.0, 742.0, 50.0, 842.0]


def test_transform_rect_rotation_90():
    # 逆时针旋转 90 度：x' = -y, y' = x
    m = (0, 1, -1, 0, 0, 0)
    out = transform_rect([1, 2, 3, 4], m)
    assert out == [-4.0, 1.0, -2.0, 3.0]


def test_invert_matrix_roundtrip():
    for m in [(2, 0, 0, 2, 10, -5), (0, 1, -1, 0, 3, 4), (1, 0.2, -0.3, 1.5, 7, -2)]:
        inv = invert_matrix(m)
        for x, y in ((1, 2), (-3, 5), (10, 10)):
            xx, yy = apply_point(inv, *apply_point(m, x, y))
            assert math.isclose(xx, x, abs_tol=1e-9) and math.isclose(yy, y, abs_tol=1e-9)


def test_validate_bbox_rules():
    vb = [0, 0, 595, 842]
    assert validate_bbox([10, 10, 20, 20], vb) == [10.0, 10.0, 20.0, 20.0]
    with pytest.raises(SchemaViolation):
        validate_bbox([20, 10, 10, 20], vb)      # 顺序非法
    with pytest.raises(SchemaViolation):
        validate_bbox([10, 10, 700, 20], vb)     # 越界
    with pytest.raises(SchemaViolation):
        validate_bbox([10, 10, float("nan"), 20], vb)


def test_validate_bbox_negative_cropbox_allowed():
    vb = [-50, -50, 500, 800]
    assert validate_bbox([-40, -40, 10, 10], vb)[0] == -40.0


def test_match_scores_geometry():
    sel = (0, 0, 100, 100)
    anchor = (0, 0, 100, 100)
    s = match_scores(sel, anchor)
    assert s["cover_anchor"] == 1.0 and s["cover_selection"] == 1.0 and s["iou"] == 1.0
    half = match_scores((0, 0, 100, 100), (0, 0, 100, 50))
    assert half["cover_anchor"] == 1.0
    assert half["cover_selection"] == 0.5
    assert rect_iou((0, 0, 10, 10), (20, 20, 30, 30)) == 0.0
