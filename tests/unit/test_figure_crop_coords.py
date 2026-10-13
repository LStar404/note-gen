"""图形裁剪坐标回归：fitz 页面坐标 y 向下，流水线 bbox y 向上，必须翻转。"""

from __future__ import annotations

import sys
import types
from pathlib import Path


def test_crop_figure_flips_y_for_fitz(monkeypatch, tmp_path):
    """曾直接把 y-up bbox 传给 fitz：裁出的是页面镜像位置的图（顶红底蓝实验实测）。"""
    calls = {}

    class FakePage:
        rect = types.SimpleNamespace(y0=0.0, y1=600.0)

        def get_pixmap(self, matrix=None, clip=None):
            calls["clip"] = clip

            class Pix:
                def save(self, p):
                    Path(p).write_bytes(b"png")

            return Pix()

    class FakeDoc:
        def __getitem__(self, i):
            return FakePage()

        def close(self):
            pass

    fake = types.SimpleNamespace(
        open=lambda p: FakeDoc(),
        Rect=lambda *a: ("RECT",) + tuple(a),
        Matrix=lambda *a: a)
    monkeypatch.setitem(sys.modules, "fitz", fake)
    from src.ingest.figure_crop import crop_figure

    out = crop_figure(tmp_path / "t.pdf", 1, [50.0, 450.0, 350.0, 550.0], tmp_path / "c.png")
    assert out is not None
    # y-up [450, 550] 在 600 高页面 → fitz y-down [50, 150]
    assert calls["clip"] == ("RECT", 50.0, 50.0, 350.0, 150.0), calls["clip"]
