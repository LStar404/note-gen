"""第一次生成：章节提纲（5.3.1）。临时 ID + DAG + token 预算分批 + 截断缩批重试。"""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

from src.core.errors import (LLMError, PriceUnknown, RetryableError,
                             SchemaViolation, TaskCancelled)
from src.core.llm import BudgetGuard, LLMClient
from src.core.text import estimate_tokens

from .prompts import (OUTLINE_PROMPT, SYSTEM_PROMPT, anchor_summary_line,
                      build_outline_schema, language_instruction)
from .validate_retry import parse_json_output, validate_outline_payload

# 实测（DeepSeek 系模型）：提纲条目输出 ≈ 锚点摘要输入的 6 倍（1129 in → 7012 out）。
# 只按输入预算分批时大批必被 max_tokens 截断 → 缩批重试（每轮都是真实调用，代价高），
# 故分批同时受「估算输出预算」约束，让一批的预期输出落在 max_tokens 之内。
_OUT_RATIO = 6.0
_OUT_RESERVE = 400  # schema/收尾安全余量


@dataclass
class OutlineResult:
    items: list[dict] = field(default_factory=list)
    complete: bool = True
    batches: int = 0
    errors: list[str] = field(default_factory=list)


def _anchor_cost(anchor: dict) -> tuple[float, float]:
    """返回（估算输入 token，估算输出 token）。"""
    cost_in = estimate_tokens(anchor_summary_line(anchor)) + 8
    return cost_in, cost_in * _OUT_RATIO


def _batch_anchors(anchors: list[dict], max_in: float, max_out: float = float("inf")) -> list[list[dict]]:
    """按 heading/阅读序分批；同时受输入预算与估算输出预算约束（5.3.2）。"""
    batches: list[list[dict]] = []
    cur: list[dict] = []
    cur_in = 0.0
    cur_out = 0.0
    reserve = 1500  # 系统提示/schema 安全余量
    for anchor in sorted(anchors, key=lambda a: (a["page"], a["reading_order"])):
        cost_in, cost_out = _anchor_cost(anchor)
        if cur and (cur_in + cost_in + reserve > max_in
                    or cur_out + cost_out + _OUT_RESERVE > max_out):
            batches.append(cur)
            cur, cur_in, cur_out = [], 0.0, 0.0
        cur.append(anchor)
        cur_in += cost_in
        cur_out += cost_out
    if cur:
        batches.append(cur)
    return batches or [[]]


def generate_outline(client: LLMClient, *, doc_id: str, chapter_id: str, manifest: dict,
                     anchors: list[dict], model: str | None, config: dict,
                     budget: BudgetGuard | None = None, task_id: str | None = None,
                     progress: Callable[[dict], None] | None = None,
                     is_cancelled: Callable[[], bool] | None = None,
                     _prefix: str = "b") -> OutlineResult:
    """逐批生成提纲。

    _prefix 是 temp_id 前缀：顶层 "b"，截断缩批递归带路径后缀（如 b0x1x0），
    保证跨调用全局唯一——temp_id 撞名会让 fill_all 把同名项折叠成一个、
    依赖错乱、块丢失（只在大章节截断缩批时触发，隐蔽且破坏性大）。
    """
    profile = manifest.get("profile", "textbook")
    chapter = next((c for c in manifest.get("chapters", []) if c["chapter_id"] == chapter_id), {})
    schema = build_outline_schema(profile)
    gen_cfg = config.get("generate", {}) or {}
    max_in = int(gen_cfg.get("outline_max_in", 32000))
    max_out = int(gen_cfg.get("outline_max_out", 16000))
    batches = _batch_anchors(anchors, max_in, max_out)
    result = OutlineResult(batches=len(batches))
    anchor_ids = {a["anchor_id"] for a in anchors}

    def _run_batch(bi: int, batch: list[dict]) -> OutlineResult:
        prefix = f"{_prefix}{bi}"
        sub = OutlineResult()
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
            if is_cancelled and is_cancelled():
                raise TaskCancelled("任务已取消")
            try:
                resp = client.complete("outline", messages, schema=schema,
                                       max_tokens=max_out,
                                       temperature=config.get("generate", {}).get("temperature"),
                                       model=model, doc_id=doc_id, chapter_id=chapter_id,
                                       task_id=task_id, budget=budget)
            except TaskCancelled:
                raise
            except (LLMError, RetryableError) as exc:
                # LLM 调用层失败按批隔离：本批记失败，其余批次照常产出，
                # outline_complete=False 保住旧块不被 5.7 淘汰。
                # 配置类错误（NO_MODEL/PRICE_UNKNOWN/模型不存在）继续冒泡，任务要清晰失败。
                sub.complete = False
                sub.errors.append(f"batch {bi}: LLM 调用失败: {exc.message}")
                return sub
            except PriceUnknown as exc:
                if exc.code != "BUDGET_EXCEEDED":
                    raise              # 配置类价格问题 → 任务清晰失败
                # 预算用尽按批隔离（与 fill 一致）：已产出批次保留，不整章陪葬
                sub.complete = False
                sub.errors.append(f"batch {bi}: {exc.message}")
                return sub
            if resp.get("finish_reason") == "length":
                # 输出截断不得视作完整提纲：缩批重试（子批前缀带路径后缀，避免 temp_id 撞名）
                if len(batch) > 1:
                    halves = [(0, batch[: len(batch) // 2]), (1, batch[len(batch) // 2:])]
                    for side, half in halves:
                        deep = generate_outline(
                            client, doc_id=doc_id, chapter_id=chapter_id, manifest=manifest,
                            anchors=half, model=model, config=config, budget=budget,
                            task_id=task_id, progress=progress, is_cancelled=is_cancelled,
                            _prefix=f"{prefix}x{side}")
                        sub.items.extend(deep.items)
                        sub.complete = sub.complete and deep.complete
                        sub.errors.extend(deep.errors)
                    return sub
                sub.complete = False
                sub.errors.append(f"batch {bi}: 输出截断且无法继续缩批")
                return sub
            try:
                payload = parse_json_output(resp.get("output") if resp.get("output") is not None
                                            else resp.get("raw_text", ""))
                items = validate_outline_payload(payload, anchor_ids=anchor_ids, profile=profile)
                for item in items:
                    item["temp_id"] = f"{prefix}-{item['temp_id']}"
                    item["depends_on"] = [f"{prefix}-{d}" if not d.startswith(prefix) else d
                                          for d in item["depends_on"]]
                sub.items.extend(items)
                return sub
            except SchemaViolation as exc:
                if attempts >= 3:
                    sub.complete = False
                    sub.errors.append(f"batch {bi}: {exc.message}")
                    return sub
                messages = messages[:1] + [{"role": "user", "content": prompt}] + [
                    {"role": "assistant", "content": json.dumps(resp.get("output"), ensure_ascii=False)[:4000]},
                    {"role": "user", "content": f"上一次输出校验失败：{exc.message}。请修正后重新输出 JSON。"},
                ]

    total = len(batches)
    workers = max(1, int(gen_cfg.get("concurrency", 2)))
    results: list[OutlineResult | None] = [None] * total
    counter_lock = threading.Lock()
    done = 0

    def _work(bi: int) -> None:
        nonlocal done
        results[bi] = _run_batch(bi, batches[bi])
        with counter_lock:
            done += 1
            n = done
        if progress:
            progress({"stage": "outline", "batch": n, "batches": total})

    if total == 1 or workers <= 1:
        for bi in range(total):
            _work(bi)
    else:
        with ThreadPoolExecutor(max_workers=min(workers, total)) as pool:
            for _ in pool.map(_work, range(total)):
                pass
    for sub in results:  # 按批次序拼接，保证确定性
        if sub is None:
            continue
        result.items.extend(sub.items)
        result.complete = result.complete and sub.complete
        result.errors.extend(sub.errors)
    return result
