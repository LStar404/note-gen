"""第一次生成：章节提纲（5.3.1）。临时 ID + DAG + token 预算分批 + 截断缩批重试。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from src.core.errors import SchemaViolation
from src.core.llm import BudgetGuard, LLMClient
from src.core.text import estimate_tokens

from .prompts import (OUTLINE_PROMPT, SYSTEM_PROMPT, anchor_summary_line,
                      build_outline_schema, language_instruction)
from .validate_retry import parse_json_output, validate_outline_payload


@dataclass
class OutlineResult:
    items: list[dict] = field(default_factory=list)
    complete: bool = True
    batches: int = 0
    errors: list[str] = field(default_factory=list)


def _batch_anchors(anchors: list[dict], max_tokens: int) -> list[list[dict]]:
    """按 heading/阅读序分批；120 字中文摘要通常已百余 token（5.3.2）。"""
    batches: list[list[dict]] = []
    cur: list[dict] = []
    cur_tokens = 0
    reserve = 1500  # 系统提示/schema 安全余量
    for anchor in sorted(anchors, key=lambda a: (a["page"], a["reading_order"])):
        line = anchor_summary_line(anchor)
        cost = estimate_tokens(line) + 8
        if cur and cur_tokens + cost + reserve > max_tokens:
            batches.append(cur)
            cur, cur_tokens = [], 0
        cur.append(anchor)
        cur_tokens += cost
    if cur:
        batches.append(cur)
    return batches or [[]]


def generate_outline(client: LLMClient, *, doc_id: str, chapter_id: str, manifest: dict,
                     anchors: list[dict], model: str | None, config: dict,
                     budget: BudgetGuard | None = None, task_id: str | None = None) -> OutlineResult:
    profile = manifest.get("profile", "textbook")
    chapter = next((c for c in manifest.get("chapters", []) if c["chapter_id"] == chapter_id), {})
    schema = build_outline_schema(profile)
    max_in = int(config.get("generate", {}).get("outline_max_in", 32000))
    batches = _batch_anchors(anchors, max_in)
    result = OutlineResult(batches=len(batches))
    anchor_ids = {a["anchor_id"] for a in anchors}

    for bi, batch in enumerate(batches):
        prompt = OUTLINE_PROMPT.format(
            doc_kind="论文" if profile == "paper" else "教材",
            book_title=manifest.get("title", ""),
            chapter_part="整篇" if profile == "paper" else f"第 {chapter.get('title', chapter_id)} 章",
            n_motivation=6,
            anchor_summaries="\n".join(anchor_summary_line(a) for a in batch),
            outline_schema=json.dumps(schema, ensure_ascii=False),
        ) + "\n" + language_instruction((config.get("generate") or {}).get("output_language"))
        messages = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt}]
        attempts = 0
        while True:
            attempts += 1
            resp = client.complete("outline", messages, schema=schema,
                                   max_tokens=8000,
                                   temperature=config.get("generate", {}).get("temperature"),
                                   model=model, doc_id=doc_id, chapter_id=chapter_id,
                                   task_id=task_id, budget=budget)
            if resp.get("finish_reason") == "length":
                # 输出截断不得视作完整提纲：缩批重试
                if len(batch) > 1:
                    halves = [batch[: len(batch) // 2], batch[len(batch) // 2:]]
                    for half in halves:
                        sub = generate_outline(client, doc_id=doc_id, chapter_id=chapter_id,
                                               manifest=manifest, anchors=half, model=model,
                                               config=config, budget=budget, task_id=task_id)
                        result.items.extend(sub.items)
                        result.complete = result.complete and sub.complete
                        result.errors.extend(sub.errors)
                    break
                result.complete = False
                result.errors.append(f"batch {bi}: 输出截断且无法继续缩批")
                break
            try:
                payload = parse_json_output(resp.get("output") if resp.get("output") is not None
                                            else resp.get("raw_text", ""))
                items = validate_outline_payload(payload, anchor_ids=anchor_ids, profile=profile)
                for item in items:
                    prefix = f"b{bi}" if len(batches) > 1 else "t"
                    item["temp_id"] = f"{prefix}-{item['temp_id']}"
                    item["depends_on"] = [f"{prefix}-{d}" if not d.startswith(prefix) else d
                                          for d in item["depends_on"]]
                result.items.extend(items)
                break
            except SchemaViolation as exc:
                if attempts >= 3:
                    result.complete = False
                    result.errors.append(f"batch {bi}: {exc.message}")
                    break
                messages = messages[:1] + [{"role": "user", "content": prompt}] + [
                    {"role": "assistant", "content": json.dumps(resp.get("output"), ensure_ascii=False)[:4000]},
                    {"role": "user", "content": f"上一次输出校验失败：{exc.message}。请修正后重新输出 JSON。"},
                ]
    return result
