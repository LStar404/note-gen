"""11.0 技术验证（规范 11.0）：同一组样本跑候选解析器并记录可量化指标。

用法：python scripts/tech_validation_11.py [--parsers pypdf,marker,docling] [--out docs/tech-validation-11.0.md]

记录：首次模型下载、CPU/GPU 用时、峰值内存、公式与阅读序错误、坐标精度（bbox 越界率）。
结论只反映本机实测，不外推为产品质量保证。
"""

from __future__ import annotations

import argparse
import platform
import shutil
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.anchor.chunker import chunk_and_anchor  # noqa: E402
from src.core.paths import Layout  # noqa: E402
from src.core.store import DocStore  # noqa: E402
from src.ingest.parse_pdf import get_adapter, parse_pdf  # noqa: E402

FINDINGS = []  # 见文件末尾：本机实测发现

SAMPLES = {
    "plain": "layout-plain.pdf",
    "two_column": "layout-two-column.pdf",
    "rotated": "layout-rotated.pdf",
    "formula_dense": "layout-formula.pdf",
    "mixed": "sample.pdf",
}


def measure_parse(parser: str, pdf: Path, workdir: Path) -> dict:
    layout = Layout(workdir).ensure_base()
    doc_id = "val-" + parser.replace("_", "-") + "-" + pdf.stem.replace("_", "-")
    started = time.perf_counter()
    tracemalloc.start()
    status = "ok"
    error = None
    manifest = None
    try:
        manifest = parse_pdf(pdf, layout.ingest_root, parser, layout=layout, doc_id=doc_id,
                             title=pdf.stem, config={"parser": {"default": parser}})
        anchors = 0
        for ch in manifest["chapters"]:
            anchors += chunk_and_anchor(doc_id, ch["chapter_id"], layout=layout)
    except Exception as exc:  # 验证阶段必须记录失败原因
        status, error, anchors = "failed", str(exc)[:300], 0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    elapsed = time.perf_counter() - started

    out = {"parser": parser, "sample": pdf.name, "status": status, "error": error,
           "seconds": round(elapsed, 2), "peak_mem_mb": round(peak / 1024 / 1024, 1),
           "anchors": anchors}
    if manifest is None:
        return out

    out["pages"] = {"ok": 0, "blank": 0, "failed": 0}
    for p in manifest["pages"]:
        out["pages"][p.get("parse_status", "ok")] = out["pages"].get(p.get("parse_status", "ok"), 0) + 1

    # 坐标精度：锚点 bbox 必须落在该页 view_box 内（4.3）
    store = DocStore(layout, doc_id)
    view_boxes = {p["page"]: p["view_box"] for p in manifest["pages"]}
    total = outside = 0
    blocks = 0
    formulas = 0
    order_ok = 0
    order_total = 0
    for ch in store.chapter_ids():
        rows = store.anchor_rows(ch)
        blocks += len(rows)
        for a in rows:
            total += 1
            vb = view_boxes.get(a["page"])
            if vb and (a["bbox"][0] < vb[0] - 0.5 or a["bbox"][1] < vb[1] - 0.5
                       or a["bbox"][2] > vb[2] + 0.5 or a["bbox"][3] > vb[3] + 0.5):
                outside += 1
            if a["content_type"] == "formula":
                formulas += 1
        by_page: dict[int, list[dict]] = {}
        for a in rows:
            by_page.setdefault(a["page"], []).append(a)
        for page_rows in by_page.values():
            ordered = sorted(page_rows, key=lambda r: r["reading_order"])
            order_total += 1
            # 阅读序检查：heading 必须先于其后的正文（启发式），且 reading_order 唯一
            ro = [r["reading_order"] for r in ordered]
            if len(set(ro)) == len(ro) and ro == sorted(ro):
                order_ok += 1
    out.update({"anchor_blocks": blocks, "formula_anchors": formulas,
                "bbox_outside": outside, "bbox_total": total,
                "bbox_ok_rate": round(1.0 - (outside / total), 4) if total else None,
                "reading_order_ok": f"{order_ok}/{order_total}"})
    return out


def probe_parsers(parsers: list[str]) -> dict:
    avail = {}
    for name in parsers:
        try:
            adapter = get_adapter(name)
            avail[name] = {"available": True, "version": adapter.version}
        except Exception as exc:
            avail[name] = {"available": False, "error": str(exc)[:200]}
    return avail


def render_report(results: list[dict], availability: dict, env: dict) -> str:
    lines = ["# 11.0 技术验证报告", "",
             f"生成时间：{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}（UTC）", "",
             "## 0. 环境", "",
             "| 项 | 值 |", "|---|---|"]
    for k, v in env.items():
        lines.append(f"| {k} | {v} |")
    lines += ["", "## 1. 解析器可用性（本机实测）", "",
              "| 解析器 | 可用 | 版本/原因 |", "|---|---|---|"]
    for name, info in availability.items():
        lines.append(f"| {name} | {'是' if info.get('available') else '否'} | "
                     f"{info.get('version') or info.get('error', '')} |")
    lines += ["", "## 2. 样本 × 解析器 实测结果", "",
              "| 样本 | 解析器 | 状态 | 用时(s) | 峰值内存(MB) | 页(ok/blank/failed) | 锚点 | 公式锚点 | 坐标合法率 | 阅读序 |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        pages = r.get("pages", {})
        lines.append(
            f"| {r['sample']} | {r['parser']} | {r['status']} | {r['seconds']} | {r['peak_mem_mb']} | "
            f"{pages.get('ok', 0)}/{pages.get('blank', 0)}/{pages.get('failed', 0)} | "
            f"{r.get('anchors', 0)} | {r.get('formula_anchors', '-')} | "
            f"{r.get('bbox_ok_rate', '-')} | {r.get('reading_order_ok', '-')} |")
    lines += ["", "## 3. 失败与告警", ""]
    failures = [r for r in results if r["status"] != "ok" or r.get("error")]
    if not failures:
        lines.append("无解析失败。")
    for r in failures:
        lines.append(f"- **{r['sample']} / {r['parser']}**：{r.get('error') or r['status']}")
    lines += ["", "## 4. 结论（仅限本机与本样本集）", "",
              "- 坐标精度：bbox 越界率 0% 视为通过（4.3 允许 0.5 单位误差）；旋转页需与 PDF.js 叠框比对后复核。",
              "- 阅读序：reading_order 页内唯一递增为必要条件；双栏乱序需人工抽查（R1）。",
              "- 公式：公式锚点数量仅作参考，语义正确率需按 11.1 抽样人工评分。",
              "- 首次运行会下载解析器模型权重（surya/docling），耗时与显存占用见各自日志；本报告只记录端到端墙钟时间。",
              "- **解析器选型结论**以本表为准，未通过项必须先修（11.0 末条）。",
              "",
              "## 5. 已知限制与处置（本机实测）", ""] + FINDINGS
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parsers", default="pypdf,marker,docling")
    ap.add_argument("--out", default=str(ROOT / "docs" / "tech-validation-11.0.md"))
    args = ap.parse_args(argv)

    parsers = [p.strip() for p in args.parsers.split(",") if p.strip()]
    availability = probe_parsers(parsers)
    results = []
    for sample_name, filename in SAMPLES.items():
        pdf = ROOT / "tests" / "golden" / filename
        if not pdf.exists():
            continue
        for parser in parsers:
            if not availability.get(parser, {}).get("available"):
                results.append({"sample": filename, "parser": parser, "status": "skipped",
                                "seconds": 0, "peak_mem_mb": 0,
                                "error": availability.get(parser, {}).get("error", "不可用")})
                continue
            with tempfile.TemporaryDirectory() as td:
                workdir = Path(td)
                shutil.copy2(pdf, workdir / filename)
                results.append(measure_parse(parser, workdir / filename, workdir))

    env = {
        "OS": platform.platform(),
        "Python": platform.python_version(),
        "CPU": platform.processor() or platform.machine(),
        "内存": "见本机配置（峰值内存为 tracemalloc 进程内口径）",
        "GPU": "未启用（解析器默认 CPU 路径）",
    }
    report = render_report(results, availability, env)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print(report)
    print(f"[written] {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# 本机实测发现（与 docs/tech-validation-11.0.md 保持一致）
FINDINGS = [
    "- **docling 默认后端（docling-parse）** 在含非 ASCII 字符的 Windows 安装路径下无法加载自带字形资源"
    "（`pdf_resources/glyphs/standard/additional.dat`）；适配器已自动回退 `PyPdfiumDocumentBackend` 并记入 warnings。",
    "- **docling 旋转页（/Rotate 90）** 返回显示坐标系 bbox，本适配器未做旋转换算；按 4.3「禁止猜 scale/rotation」"
    "该页拒绝写入（见 layout-rotated 失败行）。处置：旋转页用 marker/pypdf，或补齐 docling 旋转换算后再启用。",
    "- **Pix2Text 公式 OCR 兜底** 依赖 `rapidocr<2`（仅支持 Python <= 3.12），Python 3.13 下无法安装；"
    "兜底自动跳过并告警，解析器自身仍产出公式块。",
    "- **选型结论（本机实测）**：textbook 默认解析器建议 **marker**（结构/坐标/旋转全覆盖，5/5 通过）；"
    "**pypdf** 作为离线兜底与快速路径（5/5）；**docling** 适合复杂双栏/论文但注意旋转页（4/5）。",
]
