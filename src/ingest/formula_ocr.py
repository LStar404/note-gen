"""公式识别兜底（5.1.3）：Pix2Text 对低置信度数学裁剪补识别。

优先 Marker/Docling 输出；仅当解析器输出为空或明显乱码时替换。pix2text 可装在主环境或
解析器 venv（经 parser_workers 子进程调用，ADR D31）；不可用时返回 None 并记入报告。
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


class FormulaOCR:
    def __init__(self, engine: str = "pix2text", python: str | None = None):
        self.engine = engine
        self.python = python
        self._p2t = None

    def _load_local(self):
        if self._p2t is None:
            from pix2text import Pix2Text  # type: ignore

            self._p2t = Pix2Text()
        return self._p2t

    def recognize(self, pdf_path: str | Path, page: int, bbox: list[float]) -> str | None:
        """对指定 bbox 裁剪识别为 LaTeX；失败返回 None（不阻塞管线，记入报告）。"""
        from .figure_crop import crop_figure

        with tempfile.TemporaryDirectory() as td:
            png = crop_figure(pdf_path, page, bbox, Path(td) / "formula.png")
            if not png:
                return None
            # 1) 本环境进程内
            try:
                p2t = self._load_local()
                res = p2t.recognize(str(png))
                text = res.get("text") if isinstance(res, dict) else str(res)
                if text:
                    return text
            except Exception:
                pass
            # 2) 解析器 venv worker（隔离依赖）
            try:
                worker = Path(__file__).resolve().parent / "parser_workers.py"
                python = self.python or sys.executable
                proc = subprocess.run(
                    [python, str(worker), "pix2text"],
                    input=json.dumps({"items": [{"id": "formula", "image_path": str(png)}]},
                                     ensure_ascii=False).encode("utf-8"),
                    capture_output=True,
                    cwd=str(Path(__file__).resolve().parents[2]), timeout=600)
                if proc.returncode == 0:
                    out = json.loads(proc.stdout.decode("utf-8"))
                    for item in out.get("results", []):
                        if item.get("latex"):
                            return item["latex"]
            except Exception:
                return None
        return None
