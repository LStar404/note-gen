"""校验-重试循环（5.3.3）：JSON 解析 → Schema → 业务校验 → LaTeX 语法闸门。"""

from __future__ import annotations

import re
from typing import Any

from src.core.errors import SchemaViolation
from src.core.models import find_quote_anchor, snap_quote, validate_type_constraints

MATH_PATTERN = re.compile(r"(\$\$.*?\$\$|\$.*?\$|\\\(.*?\\\)|\\\[.*?\\\])", re.DOTALL)
_FENCE = chr(96) * 3
CODE_FENCE = re.compile(_FENCE + ".*?" + _FENCE, re.DOTALL)


def parse_json_output(text: str) -> Any:
    import json

    from src.core.text import repair_json_escapes

    if isinstance(text, (dict, list)):
        return text
    if isinstance(text, str):
        # 裸 LaTeX 撞上 JSON 转义（"\bigcap" → 0x08 + "igcap"）必须在 json.loads 之前修，
        # 否则 json 会把 \b/\t 当合法转义悄悄吃掉反斜杠（渲染成 "Digcap" 这类破公式）
        text = repair_json_escapes(text)
    try:
        return json.loads(text)
    except Exception:
        pass
    from src.core.llm import json_repair_loads

    out = json_repair_loads(text)
    if isinstance(out, str):
        raise SchemaViolation("模型输出不是合法 JSON")
    return out


# ---- Schema 校验（附录 A 的权威定义，逐字段实现） ----
def validate_outline_payload(payload: dict, *, anchor_ids: set[str], profile: str,
                             check_quotes: bool = True) -> list[dict]:
    if not isinstance(payload, dict) or not isinstance(payload.get("blocks"), list):
        raise SchemaViolation("outline 输出缺少 blocks 数组")
    blocks = payload["blocks"]
    from src.core.text import sanitize_control_chars

    for item in blocks:
        if isinstance(item, dict) and isinstance(item.get("title"), str):
            item["title"] = sanitize_control_chars(item["title"])
    from src.core.block_types import type_enum

    allowed_types = set(type_enum(profile))
    temp_ids: set[str] = set()
    out: list[dict] = []
    for i, item in enumerate(blocks):
        if not isinstance(item, dict):
            raise SchemaViolation(f"blocks[{i}] 不是对象")
        for field in ("temp_id", "type", "title", "basis", "sources", "depends_on", "order"):
            if field not in item:
                raise SchemaViolation(f"blocks[{i}] 缺少必填字段 {field}")
        temp_id = str(item["temp_id"])
        if not re.match(r"^[A-Za-z0-9-]{1,64}$", temp_id):
            raise SchemaViolation(f"temp_id 非法: {temp_id}")
        if temp_id in temp_ids:
            raise SchemaViolation(f"temp_id 重复: {temp_id}")
        temp_ids.add(temp_id)
        if item["type"] not in allowed_types:
            raise SchemaViolation(f"blocks[{i}].type 非法: {item['type']}")
        if len(str(item["title"])) > 80:
            raise SchemaViolation(f"blocks[{i}].title 超过 80 字符")
        if item["basis"] not in ("anchored", "synthesis"):
            raise SchemaViolation(f"blocks[{i}].basis 非法")
        if not isinstance(item["sources"], list) or not isinstance(item["depends_on"], list):
            raise SchemaViolation(f"blocks[{i}] sources/depends_on 必须是数组")
        try:
            float(item["order"])
        except Exception:
            raise SchemaViolation(f"blocks[{i}].order 必须是数值")
        for src in item["sources"]:
            if not isinstance(src, dict) or not {"anchor_id", "quote", "relation"} <= set(src):
                raise SchemaViolation(f"blocks[{i}].sources 元素非法")
            if check_quotes and src["anchor_id"] in anchor_ids:
                pass
            if src["anchor_id"] not in anchor_ids:
                raise SchemaViolation(f"sources 引用不存在的锚点: {src['anchor_id']}",
                                      code="ANCHOR_NOT_FOUND")
        out.append(item)

    for item in out:
        for dep in item["depends_on"]:
            if dep == item["temp_id"]:
                raise SchemaViolation(f"depends_on 自引用: {item['temp_id']}")
            if dep not in temp_ids:
                raise SchemaViolation(f"depends_on 引用不存在的 temp_id: {dep}")
    _check_dag(out)
    return out


def _check_dag(items: list[dict]) -> None:
    graph = {i["temp_id"]: list(i["depends_on"]) for i in items}
    state: dict[str, int] = {}

    def visit(node: str, stack: tuple):
        if state.get(node) == 2:
            return
        if state.get(node) == 1:
            raise SchemaViolation(f"depends_on 存在环: {' -> '.join(stack + (node,))}")
        state[node] = 1
        for dep in graph.get(node, []):
            visit(dep, stack + (node,))
        state[node] = 2

    for node in graph:
        visit(node, tuple())


def validate_fill_payload(payload: dict, *, allowed_anchor_ids: set[str],
                          anchors_by_id: dict[str, dict], spec: dict,
                          check_quotes: bool = True) -> dict:
    if not isinstance(payload, dict):
        raise SchemaViolation("fill 输出不是对象")
    # 控制字符净化（入库闸门，见 core.text.sanitize_control_chars）：
    # JSON 转义撞车会把裸 LaTeX 的 \b/\t/\f/\n/\r/\u 吃成控制字符——
    # \bigcap 存成 0x08+"igcap"，渲染出来正是用户看到的 "Digcap" 破公式。
    # 无论上游怎么犯，存储层都不再被污染。
    from src.core.text import (normalize_math_text, repair_stray_latex,
                               sanitize_control_chars)

    for key in ("content", "title"):
        v = payload.get(key)
        if isinstance(v, str):
            # 先还原控制字符，再修文本段越界命令（\Rightarrow 等包进数学、
            # \href 转链接），最后公式规范化（跨行行内公式/丢反斜杠/裸希腊字母）
            payload[key] = normalize_math_text(repair_stray_latex(sanitize_control_chars(v)))
    # 死图占位符：不在本块来源里的 ![{fig}] 一律丢掉（前端只会拿到 404 隐形破图）
    if isinstance(payload.get("content"), str):
        payload["content"] = strip_placeholder_figures(payload["content"], spec)
    for field in ("content", "content_format", "tags", "sources_patch", "basis"):
        if field not in payload:
            raise SchemaViolation(f"fill 输出缺少必填字段 {field}")
    if payload["content_format"] not in ("md_latex", "tex", "plain"):
        raise SchemaViolation(f"content_format 非法: {payload['content_format']}")
    if payload["basis"] not in ("anchored", "synthesis"):
        raise SchemaViolation(f"basis 非法: {payload['basis']}")
    if not isinstance(payload["tags"], list):
        raise SchemaViolation("tags 必须是数组")
    if len(payload["content"].encode("utf-8")) > 50 * 1024:
        raise SchemaViolation("content 超过 50 KiB")
    sources = payload["sources_patch"]
    if not isinstance(sources, list):
        raise SchemaViolation("sources_patch 必须是数组")
    for src in sources:
        if not isinstance(src, dict):
            raise SchemaViolation("sources_patch 元素必须是对象")
        for field in ("anchor_id", "quote", "relation"):
            if field not in src:
                raise SchemaViolation(f"sources_patch 元素缺少 {field}")
        if src["anchor_id"] not in allowed_anchor_ids:
            raise SchemaViolation(f"sources_patch 引用了未提供的锚点: {src['anchor_id']}",
                                  code="ANCHOR_NOT_FOUND")
        if len(str(src["quote"])) > 300:
            raise SchemaViolation("quote 超过 300 字符")
        if check_quotes:
            anchor = anchors_by_id.get(src["anchor_id"])
            if anchor is not None:
                # 4.6.1：先把 quote 吸附回原文逐字片段（模型空白/标点/轻微改写都被纠正），
                # 真编造（定位不到）才判失败——否则整块被丢，实测 ch08 因此丢 22/61 块
                fixed = snap_quote(anchor.get("content", ""), str(src["quote"]))
                if fixed is None:
                    # 模型可能把 B 锚点的原文挂到 A 锚点名下：在允许集里找逐字命中，
                    # 唯一命中则改挂回真实出处（多命中不猜，仍判失败）
                    hit = find_quote_anchor(str(src["quote"]), anchors_by_id, set(allowed_anchor_ids))
                    if hit is not None:
                        src["anchor_id"], fixed = hit
                if fixed is None:
                    raise SchemaViolation(f"quote 不匹配锚点原文: {src['anchor_id']}")
                src["quote"] = fixed
    # 4.6.1 归一：同一 anchor_id 在一块内不得重复、sources ≤ 5。模型偶发重复/超量时
    # 就地去重截断（保留首次出现），否则要到 commit 才被 NoteBlock 校验拦下，整个章节失败。
    dedup: list[dict] = []
    seen: set[str] = set()
    for src in sources:
        if src["anchor_id"] in seen:
            continue
        seen.add(src["anchor_id"])
        dedup.append(src)
    sources = dedup[:5]
    payload["sources_patch"] = sources
    validate_type_constraints(spec["type"], basis=payload["basis"], source_count=len(sources))
    return payload


def validate_sources_against_anchors(block_type: str, basis: str, sources: list[dict],
                                     anchors_by_id: dict[str, dict]) -> None:
    for src in sources:
        if src["anchor_id"] not in anchors_by_id:
            raise SchemaViolation(f"来源锚点不存在: {src['anchor_id']}", code="ANCHOR_NOT_FOUND")
    validate_type_constraints(block_type, basis=basis, source_count=len(sources))


# KaTeX 未定义的"简写宏"：模型爱用 \F \E \Var \tr 这类简写，KaTeX 一律渲染成红字
# （实测 katex 0.16：未定义命令走 mathcolor="#cc0000"）。这些不是语法错误，
# pylatexenc 解析得过去，只能在闸门里按名单拦下并给出建议写法。
# 注：\R \N \Z \H \S \P 在 KaTeX 里有定义，不在此列。
UNDEFINED_MACROS: dict[str, str] = {
    "F": r"\mathcal{F}", "E": r"\mathbb{E}", "C": r"\mathbb{C}", "Q": r"\mathbb{Q}",
    "A": r"\mathcal{A}", "B": r"\mathcal{B}", "D": r"\mathcal{D}", "G": r"\mathcal{G}",
    "I": r"\mathcal{I}", "J": r"\mathcal{J}", "K": r"\mathcal{K}", "L": r"\mathcal{L}",
    "M": r"\mathcal{M}", "O": r"\mathcal{O}", "T": r"\mathcal{T}", "U": r"\mathcal{U}",
    "V": r"\mathcal{V}", "W": r"\mathcal{W}", "X": r"\mathcal{X}", "Y": r"\mathcal{Y}",
    "Var": r"\operatorname{Var}", "Cov": r"\operatorname{Cov}", "tr": r"\operatorname{tr}",
    "rank": r"\operatorname{rank}", "diag": r"\operatorname{diag}",
    "im": r"\operatorname{im}", "span": r"\operatorname{span}",
    "grad": r"\nabla", "Prob": r"\mathbb{P}", "Ex": r"\mathbb{E}",
    "ind": r"\mathbf{1}", "vol": r"\operatorname{vol}", "KL": r"D_{\mathrm{KL}}",
}
_MACRO_RE = re.compile(r"\\([A-Za-z]+)")

# 图占位符：模型在没有真实 figure_id 时会写 ![{fig}] 占位（历史上填成 fig_00000000），
# 前端只会得到一个 404 隐形破图。闸门统一丢掉「不在本块来源里」的图引用。
_FIG_PLACEHOLDER_RE = re.compile(r"[ \t]*!\[(fig_\d{8})\][ \t]*\n*")


def _allowed_figure_ids(spec: dict) -> set[str]:
    return {s.get("figure_id") for s in (spec.get("sources") or []) if s.get("figure_id")}


def strip_placeholder_figures(content: str, spec: dict) -> str:
    """删除引用不到真实图资源的 ![{fig_id}] 占位符（保留图注与解读文字）。"""
    allowed = _allowed_figure_ids(spec)

    def _sub(m: "re.Match[str]") -> str:
        return m.group(0) if m.group(1) in allowed else ""

    return _FIG_PLACEHOLDER_RE.sub(_sub, content or "")


# ---- LaTeX 语法闸门（5.3.3，v1.5） ----
def extract_math_segments(content: str) -> list[str]:
    text = CODE_FENCE.sub("", content or "")
    return [m.group(0) for m in MATH_PATTERN.finditer(text)]


def check_latex(content: str) -> tuple[bool, list[str]]:
    """语法检查（非编译保证）：pylatexenc 严格解析 + 括号/环境平衡。"""
    warnings: list[str] = []
    segments = extract_math_segments(content)
    try:
        from pylatexenc.latexwalker import LatexWalker
    except Exception:
        return True, ["pylatexenc 不可用，已跳过 LaTeX 检查"]
    ok = True
    for seg in segments:
        body = seg.strip()
        if body.startswith("$$") and body.endswith("$$") and len(body) > 4:
            body = body[2:-2]
        elif body.startswith("$") and body.endswith("$") and len(body) > 2:
            body = body[1:-1]
        elif body.startswith("\\(") and body.endswith("\\)"):
            body = body[2:-2]
        elif body.startswith("\\[") and body.endswith("\\]"):
            body = body[2:-2]
        try:
            LatexWalker(body, tolerant_parsing=False).get_latex_nodes()
        except Exception as exc:
            ok = False
            warnings.append(f"LaTeX 语法错误: {exc} @ {seg[:40]}")
            continue
        if body.count("{") != body.count("}"):
            ok = False
            warnings.append(f"花括号不配对: {seg[:40]}")
        # 未定义宏：KaTeX 渲染成红字，用户看到的就是"公式坏了"（pylatexenc 解析得过去，
        # 必须在闸门里按名单拦下，并给出建议写法供模型自我修正）
        for name in dict.fromkeys(_MACRO_RE.findall(body)):
            if name in UNDEFINED_MACROS:
                ok = False
                warnings.append(f"未定义命令 \\{name}（KaTeX 渲染为红字），请改用 "
                                f"{UNDEFINED_MACROS[name]}")
    return ok, warnings
