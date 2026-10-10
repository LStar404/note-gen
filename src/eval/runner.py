"""评测集执行器（10.5）：faithfulness / formula / coverage 三维度 + 基线对比。

- 用例：tests/eval/cases.jsonl（{case_id, doc_id, chapter_id, pdf_sha256, anchor_id,
  expected_type, expected_quote, reference_formula, dimension}）。
- 人工标签：tests/eval/labels.json（{"case_id": {"faithfulness": 0-1, "formula": 0-1, "coverage": 0-1}}），
  存在时优先于自动指标；标签文件须随评测集版本化。
- 回归触发（prompt_version/解析器/生成参数变更后必须跑）：任一维度低于基线 5 个百分点即告警。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from src.core.jsonl import load_jsonl
from src.core.paths import Layout
from src.core.store import DocStore

DIMENSIONS = ("faithfulness", "formula", "coverage")
REGRESSION_THRESHOLD = 0.05

MATH_RE = re.compile(r"(\$\$.*?\$\$|\$.*?\$)", re.DOTALL)


def normalize_latex(text: str) -> str:
    """公式对拍归一化：去空白/尺寸修饰/定界符差异，保留语义符号。"""
    s = text.strip()
    s = re.sub(r"^\$\$|\$\$$", "", s)
    s = re.sub(r"^\$|\$$", "", s)
    s = s.replace("\\left", "").replace("\\right", "")
    s = s.replace("\\!", "").replace("\\,", "").replace("\\;", "").replace("\\ ", " ")
    s = s.replace("\\,", "").replace("{", "").replace("}", "")
    s = re.sub(r"\s+", "", s)
    return s


def _block_matches(store: DocStore, case: dict) -> dict | None:
    """定位该用例对应的生成块：优先按 anchor_id 命中来源，其次按 quote 内容命中。"""
    anchor_id = case.get("anchor_id")
    expected_quote = (case.get("expected_quote") or "").strip()
    expected_type = case.get("expected_type")
    candidates = []
    for block in store.all_block_rows():
        if block.get("deleted_at"):
            continue
        if expected_type and block.get("type") != expected_type:
            continue
        hit_anchor = any(s.get("anchor_id") == anchor_id for s in block.get("sources", []))
        hit_quote = bool(expected_quote) and (
            expected_quote in block.get("content", "")
            or any(expected_quote in (s.get("quote") or "") for s in block.get("sources", []))
        )
        if hit_anchor or hit_quote:
            candidates.append((block, hit_anchor))
    if not candidates:
        return None
    # 同锚点多块时取已锚定来源最匹配者（不任取 order 最小）
    candidates.sort(key=lambda t: (not t[1], -float(t[0].get("order", 0.0))))
    return candidates[0][0]


def _formula_score(block: dict | None, case: dict, label: dict | None) -> float:
    if label and "formula" in label:
        return float(label["formula"])
    reference = (case.get("reference_formula") or "").strip()
    if not reference:
        return 1.0
    if block is None:
        return 0.0
    want = normalize_latex(reference)
    segments = [normalize_latex(m.group(0)) for m in MATH_RE.finditer(block.get("content", ""))]
    return 1.0 if want in segments or any(want in seg for seg in segments) else 0.0


def score_case(store: DocStore, case: dict, label: dict | None) -> dict[str, float]:
    block = _block_matches(store, case)
    if label and "coverage" in label:
        coverage = float(label["coverage"])
    else:
        coverage = 1.0 if block is not None else 0.0
    if label and "faithfulness" in label:
        faithfulness = float(label["faithfulness"])
    else:
        # 结构代理：期望引文出现在块正文或来源 quote 中；不替代人工忠实度评分
        quote = (case.get("expected_quote") or "").strip()
        if block is None:
            faithfulness = 0.0
        elif not quote:
            faithfulness = 1.0
        else:
            faithfulness = 1.0 if (
                quote in block.get("content", "")
                or any(quote in (s.get("quote") or "") for s in block.get("sources", []))
            ) else 0.0
    formula = _formula_score(block, case, label)
    return {"faithfulness": faithfulness, "formula": formula, "coverage": coverage}


def run_eval(layout: Layout, doc_id: str, *, cases_path: Path,
             labels_path: Path | None = None, baseline_path: Path | None = None,
             update_baseline: bool = False) -> dict[str, Any]:
    if not cases_path.exists():
        raise FileNotFoundError(f"缺少评测集: {cases_path}")
    cases = [c for c in load_jsonl(cases_path) if not c.get("doc_id") or c["doc_id"] == doc_id]
    labels: dict[str, dict] = {}
    if labels_path and labels_path.exists():
        labels = json.loads(labels_path.read_text(encoding="utf-8"))

    store = DocStore(layout, doc_id)
    per_case = []
    buckets: dict[str, list[float]] = {d: [] for d in DIMENSIONS}
    for case in cases:
        label = labels.get(case.get("case_id", ""))
        scores = score_case(store, case, label)
        per_case.append({"case_id": case.get("case_id"), "dimension": case.get("dimension"),
                         "block_matched": _block_matches(store, case) is not None,
                         "scores": scores, "human_labelled": bool(label)})
        for dim in DIMENSIONS:
            buckets[dim].append(scores[dim])

    result: dict[str, Any] = {
        "doc_id": doc_id,
        "cases": len(cases),
        "human_labelled": sum(1 for c in per_case if c["human_labelled"]),
        "dimensions": {d: (sum(v) / len(v) if v else 0.0) for d, v in buckets.items()},
        "detail": per_case,
    }

    baseline = None
    if baseline_path and baseline_path.exists():
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    if update_baseline:
        result["baseline_written"] = True
        if baseline_path:
            baseline_path.parent.mkdir(parents=True, exist_ok=True)
            baseline_path.write_text(
                json.dumps({"doc_id": doc_id, "dimensions": result["dimensions"]},
                           ensure_ascii=False, indent=2), encoding="utf-8")
    elif baseline and "dimensions" in baseline:
        result["baseline"] = baseline["dimensions"]
        result["regressions"] = [
            {"dimension": d, "baseline": baseline["dimensions"].get(d),
             "current": result["dimensions"].get(d)}
            for d in DIMENSIONS
            if d in baseline["dimensions"]
            and result["dimensions"][d] < float(baseline["dimensions"][d]) - REGRESSION_THRESHOLD
        ]
    return result
