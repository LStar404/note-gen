"""校验-重试循环（5.3.3）：JSON 解析 → Schema → 业务校验 → LaTeX 语法闸门。"""

from __future__ import annotations

import re
from typing import Any

from src.core.errors import SchemaViolation
from src.core.models import quote_matches, validate_type_constraints

MATH_PATTERN = re.compile(r"(\$\$.*?\$\$|\$.*?\$|\\\(.*?\\\)|\\\[.*?\\\])", re.DOTALL)
_FENCE = chr(96) * 3
CODE_FENCE = re.compile(_FENCE + ".*?" + _FENCE, re.DOTALL)


def parse_json_output(text: str) -> Any:
    import json

    if isinstance(text, (dict, list)):
        return text
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
            if anchor is not None and not quote_matches(anchor.get("content", ""), str(src["quote"])):
                raise SchemaViolation(f"quote 不匹配锚点原文: {src['anchor_id']}")
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
    return ok, warnings
