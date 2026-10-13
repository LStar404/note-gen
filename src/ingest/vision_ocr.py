"""视觉补 OCR（5.1.1 增强）：本地 rapidocr 认不好的扫描页，交给视觉模型重识别。

扫描书的公式/复杂排版常被本地 OCR 识别成乱码；视觉模型看图转 LaTeX 质量高得多，
且每页只花少量 token。只对「弱识别页」触发（质量启发式），成本有界。
输出带粗几何（行的垂直位置），满足 4.3 bbox 契约——锚点/选区匹配仍可用。
"""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path

_GARBAGE_RE = re.compile("[\ufffd\x00-\x08\x0e-\x1f]")
_SYMBOL_RE = re.compile(r"[^\w\u4e00-\u9fff\s，。、；：？！（）《》【】·—…,.;:?\-\[\]]")


def weak_ocr_text(text: str) -> bool:
    """识别质量启发式：空/替换符/符号密度过高/行碎而短 → 判弱，值得视觉重识别。"""
    t = (text or "").strip()
    if not t:
        return True
    if _GARBAGE_RE.search(t):
        return True
    if len(t) >= 6:
        # 公式/乱码页的特征就是符号密集（#%&*\{~^|…）；短阈值也要查——
        # 实测 7 字的 "#%&* ~^" 曾因长度门槛漏判
        sym = len(_SYMBOL_RE.findall(t))
        if sym / len(t) > 0.35:
            return True
    lines = [ln for ln in t.splitlines() if ln.strip()]
    if len(lines) >= 4:
        avg = sum(len(ln) for ln in lines) / len(lines)
        if avg < 12:
            return True
    return False


VISION_PROMPT = (
    "这是一张教材扫描页。请逐行忠实转录页面文字（含数学公式，公式用 LaTeX），"
    "不要翻译、不要总结、不要解释。只输出 JSON："
    '{"lines": [{"text": "转录内容", "y": 0.42}]}'
    "（y 是该行在页面中的垂直位置：0=页顶，1=页底，保留两位小数）。")


def build_vision_ocr(client, model: str):
    """构造「页面 PNG → 块列表」识别器；client 为 LLMClient（vision 能力模型）。

    返回的块带 frac（页面分数坐标），由调用方经 fracs_to_bbox 落到页面 view_box。"""
    def _ocr(png_path: str) -> list[dict]:
        data = Path(png_path).read_bytes()
        b64 = base64.b64encode(data).decode("ascii")
        messages = [{"role": "user", "content": [
            {"type": "text", "text": VISION_PROMPT},
            {"type": "image_url",
             "image_url": {"url": "data:image/png;base64," + b64}},
        ]}]
        resp = client.complete("ocr", messages, schema=None, max_tokens=4000,
                              temperature=0.0, model=model)
        out = resp.get("output")
        if isinstance(out, str):
            try:
                out = json.loads(out)
            except Exception:
                return []
        lines = (out or {}).get("lines") if isinstance(out, dict) else None
        if not isinstance(lines, list):
            return []
        rows = []
        for ln in lines:
            if not isinstance(ln, dict):
                continue
            t = str(ln.get("text") or "").strip()
            try:
                y = min(1.0, max(0.0, float(ln.get("y"))))
            except Exception:
                y = None
            if t:
                rows.append({"text": t, "y": y})
        rows.sort(key=lambda r: (r["y"] is None, r["y"] or 0.0))
        blocks: list[dict] = []
        for i, r in enumerate(rows):
            y0 = r["y"] if r["y"] is not None else i / max(1, len(rows))
            y1 = (rows[i + 1]["y"]
                  if (i + 1 < len(rows) and rows[i + 1]["y"] is not None)
                  else y0 + 1.0 / max(1, len(rows)))
            if y1 <= y0:
                y1 = y0 + 0.01
            blocks.append({"type": "paragraph", "level": 0, "text": r["text"],
                           "frac": [0.05, round(y0, 4), 0.95, round(min(1.0, y1), 4)]})
        return blocks
    return _ocr


def fracs_to_bbox(frac: list[float], view_box: list[float]) -> list[float]:
    """页面分数坐标（y 以页顶为 0）→ PDF 用户空间 bbox（y 向上）。"""
    x0, y0, x1, y1 = (float(v) for v in view_box)
    w = x1 - x0
    h = y1 - y0
    fx0, fy0, fx1, fy1 = frac
    return [x0 + fx0 * w, y1 - fy1 * h, x0 + fx1 * w, y1 - fy0 * h]
