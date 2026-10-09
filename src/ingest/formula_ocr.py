"""公式识别兜底（5.1 第 3 步）：Pix2Text 对低置信度数学裁剪补识别。"""

from __future__ import annotations

from pathlib import Path


class FormulaOCR:
    """优先 Marker 输出；仅当解析器输出为空或明显乱码时替换（5.1 规则）。"""

    def __init__(self, engine: str = "pix2text"):
        self.engine = engine
        self._p2t = None

    def _load(self):
        if self._p2t is None:
            try:
                from pix2text import Pix2Text  # type: ignore

                self._p2t = Pix2Text()
            except Exception as exc:
                raise RuntimeError(f"Pix2Text 不可用（需安装 [formula] 额外依赖并下载权重）: {exc}")
        return self._p2t

    def recognize(self, pdf_path: str | Path, page: int, bbox: list[float]) -> str | None:
        """对指定 bbox 裁剪识别为 LaTeX；失败返回 None（不阻塞管线，记入报告）。"""
        try:
            p2t = self._load()
        except Exception:
            return None
        try:
            from .figure_crop import crop_figure
            import tempfile

            with tempfile.TemporaryDirectory() as td:
                png = crop_figure(pdf_path, page, bbox, Path(td) / "formula.png")
                if not png:
                    return None
                result = p2t.recognize(str(png))
            if isinstance(result, dict):
                return result.get("text") or result.get("latex")
            return str(result) if result else None
        except Exception:
            return None
