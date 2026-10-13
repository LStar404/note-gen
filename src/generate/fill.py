"""第二次生成：逐块填充（5.3.1/5.3.2）；依赖目标先完成，批大小 = concurrency。"""

from __future__ import annotations

import json
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Callable

from src.core.errors import (LLMError, PriceUnknown, QuotaExhausted, RetryableError,
                             SchemaViolation, TaskCancelled)
from src.core.block_types import type_guide
from src.core.llm import BudgetGuard, LLMClient
from src.core.text import estimate_tokens

from .prompts import (FILL_MANY_PROMPT, FILL_PROMPT, SYSTEM_PROMPT, build_fill_many_schema,
                      build_fill_schema, compact_schema, language_instruction)
from .validate_retry import check_latex, parse_json_output, validate_fill_payload


def _adjacent_anchors(anchors: list[dict], anchor_ids: list[str], k: int = 2) -> list[dict]:
    ordered = sorted(anchors, key=lambda a: (a["page"], a["reading_order"]))
    idx = {a["anchor_id"]: i for i, a in enumerate(ordered)}
    out: list[dict] = []
    for aid in anchor_ids:
        i = idx.get(aid)
        if i is None:
            continue
        for j in range(max(0, i - k), min(len(ordered), i + k + 1)):
            if ordered[j]["anchor_id"] not in anchor_ids and ordered[j] not in out:
                out.append(ordered[j])
    return out


def fill_one(client: LLMClient, *, doc_id: str, chapter_id: str, spec: dict,
             anchors_by_id: dict[str, dict], anchors: list[dict], model: str | None,
             config: dict, related_titles: list[str], budget: BudgetGuard | None = None,
             task_id: str | None = None) -> dict:
    """单块填充；返回填充结果（含失败原因，不静默丢来源）。"""
    profile = config.get("_profile", "textbook")
    schema = build_fill_schema(profile)
    source_ids = [s["anchor_id"] for s in spec.get("sources", []) if s.get("anchor_id") in anchors_by_id]
    fulltexts = "\n\n".join(f"[{aid}] {anchors_by_id[aid].get('content', '')[:800]}" for aid in source_ids)
    adj = _adjacent_anchors(anchors, source_ids)
    if adj:
        fulltexts += "\n\n相邻锚点（仅供理解语境，不得作为来源）：\n" + "\n".join(
            f"[{a['anchor_id']}] {a.get('content', '')[:80]}" for a in adj)
    # 相关块标题不再注入（省 token；依赖语境已由批内 depends_on 标题携带）

    # 图占位符 id 必须用真实 figure_id（原实现误取 anchor_id，模型照抄后全是破图）
    fig_id = next((s.get("figure_id") for s in spec.get("sources", []) if s.get("figure_id")),
                  "fig_00000000")
    prompt = FILL_PROMPT.format(
        block_type=spec.get("type", "definition"),
        title=spec.get("title", ""),
        fig_id=fig_id,
        block_spec_json=json.dumps({k: spec.get(k) for k in
                                    ("temp_id", "type", "title", "basis", "sources", "depends_on")},
                                   ensure_ascii=False),
        source_fulltexts=fulltexts,
        type_guide=type_guide(profile),
        fill_schema=compact_schema(schema),
    ) + "\n" + language_instruction((config.get("generate") or {}).get("output_language"))
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}]
    max_in = int(config.get("generate", {}).get("fill_max_in", 8000))
    if estimate_tokens(prompt) > max_in:
        # 超预算：先裁剪来源原文（保留锚点 id 与结构），仍超限再裁剪相邻上下文（5.3.2）
        shorter = "\n\n".join(f"[{aid}] {anchors_by_id[aid].get('content', '')[:600]}" for aid in source_ids)
        prompt = FILL_PROMPT.format(
            block_type=spec.get("type", "definition"),
            title=spec.get("title", ""),
            fig_id=fig_id,
            block_spec_json=json.dumps({k: spec.get(k) for k in
                                        ("temp_id", "type", "title", "basis", "sources", "depends_on")},
                                       ensure_ascii=False),
            source_fulltexts=shorter,
            type_guide=type_guide(profile),
            fill_schema=compact_schema(schema),
        ) + "\n" + language_instruction((config.get("generate") or {}).get("output_language"))
        messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}]

    # 输出预算（5.3.2 可配置）：写死 2000 会把长块 JSON 截断成非法输出，
    # 重试 3 次仍失败 → 整块被丢（实测 ch06 因此丢了 12/48 块）
    max_out = int(config.get("generate", {}).get("fill_max_out", 6000))
    errors: list[str] = []
    for attempt in range(4):           # 首次 + 最多 3 次修复
        try:
            resp = client.complete("fill", messages, schema=schema, max_tokens=max_out,
                                   temperature=config.get("generate", {}).get("temperature"),
                                   model=model, doc_id=doc_id, chapter_id=chapter_id,
                                   task_id=task_id, budget=budget)
        except TaskCancelled:
            raise
        except QuotaExhausted as exc:
            # 配额耗尽按块隔离（而不是炸穿整章）：本块标失败=旧块保留，
            # 已产出结果照常落缓存——下次生成自动续填失败块、复用已完成块
            errors.append(str(exc.message))
            return {"_temp_id": spec["temp_id"], "_status": "failed", "_errors": errors,
                    "content": "", "content_format": "md_latex", "tags": [],
                    "sources_patch": spec.get("sources", []), "basis": spec.get("basis", "anchored")}
        except (LLMError, RetryableError) as exc:
            # LLM 调用层失败按块隔离（余额耗尽/超时/限流）：记失败原因并返回，
            # 不得冒泡拖垮整章——否则已完成的填充全部丢弃（实测 402 中断丢掉 32 块成果）。
            # 注意：配置类错误（未配置模型/价格未知/模型不存在）不属于此类，必须冒泡，
            # 否则任务会「成功」而全部块悄悄失败（probe 实测过这种假成功）。
            errors.append(f"LLM 调用失败: {exc.message}")
            return {"_temp_id": spec["temp_id"], "_status": "failed", "_errors": errors,
                    "content": "", "content_format": "md_latex", "tags": [],
                    "sources_patch": spec.get("sources", []), "basis": spec.get("basis", "anchored")}
        except PriceUnknown as exc:
            if exc.code != "BUDGET_EXCEEDED":
                raise                     # 配置类价格问题 → 任务清晰失败
            # 预算用尽（5.3.5 停止新调用）：同样按块隔离，保住已完成成果
            errors.append(f"LLM 调用失败: {exc.message}")
            return {"_temp_id": spec["temp_id"], "_status": "failed", "_errors": errors,
                    "content": "", "content_format": "md_latex", "tags": [],
                    "sources_patch": spec.get("sources", []), "basis": spec.get("basis", "anchored")}
        try:
            payload = parse_json_output(resp.get("output") if resp.get("output") is not None
                                        else resp.get("raw_text", ""))
            validate_fill_payload(payload, allowed_anchor_ids=set(source_ids),
                                  anchors_by_id=anchors_by_id, spec=spec)
            ok, latex_warnings = check_latex(payload.get("content", ""))
            if not ok:
                if attempt < 1:
                    raise SchemaViolation("；".join(latex_warnings))
                payload.setdefault("_warnings", []).extend(latex_warnings)
                payload["_status"] = "needs_review"
            else:
                payload.setdefault("_warnings", []).extend(latex_warnings)
                payload["_status"] = "ok"
            payload["_temp_id"] = spec["temp_id"]
            return payload
        except SchemaViolation as exc:
            errors.append(str(exc.message))
            hint = f"上一次输出校验失败：{exc.message}。请修正后重新输出完整 JSON。"
            if "quote" in exc.message:
                hint += ("（quote 必须逐字照抄锚点原文的连续片段，"
                         "不得改写、翻译、加省略号或补标点；可从原文截取 ≤300 字。）")
            if "不是合法 JSON" in exc.message or "缺少必填字段" in exc.message:
                hint += "（输出必须是单个完整 JSON 对象，禁止 markdown 代码围栏与截断。）"
            messages = messages[:2] + [
                {"role": "assistant", "content": json.dumps(resp.get("output"), ensure_ascii=False)[:4000]},
                {"role": "user", "content": hint},
            ]
    return {"_temp_id": spec["temp_id"], "_status": "failed", "_errors": errors,
            "content": "", "content_format": "md_latex", "tags": [],
            "sources_patch": spec.get("sources", []), "basis": spec.get("basis", "anchored")}




def _failed_result(spec: dict, errors: list[str]) -> dict:
    return {"_temp_id": spec["temp_id"], "_status": "failed", "_errors": errors,
            "content": "", "content_format": "md_latex", "tags": [],
            "sources_patch": spec.get("sources", []), "basis": spec.get("basis", "anchored")}


def _fill_many_parts(specs: list[dict], anchors_by_id: dict[str, dict],
                     by_id: dict[str, dict], src_chars: int) -> tuple[str, dict[str, list[str]]]:
    """拼装批量提示词的块资料段；返回 (资料文本, temp_id→允许锚点 id)。"""
    parts: list[str] = []
    allowed: dict[str, list[str]] = {}
    for i, spec in enumerate(specs):
        source_ids = [s["anchor_id"] for s in spec.get("sources", [])
                      if s.get("anchor_id") in anchors_by_id]
        allowed[spec["temp_id"]] = source_ids
        fulltexts = "\n\n".join(
            f"[{aid}] {anchors_by_id[aid].get('content', '')[:src_chars]}" for aid in source_ids)
        fig_id = next((s.get("figure_id") for s in spec.get("sources", [])
                       if s.get("figure_id")), "fig_00000000")
        related = [by_id[d]["title"] for d in spec.get("depends_on", []) if d in by_id]
        parts.append(
            f"【块{i + 1}】temp_id={spec.get('temp_id')}　图片占位 ![{fig_id}]"
            + (f"　依赖块标题：{'；'.join(related[:4])}" if related else "")
            + "\n块定义："
            + json.dumps({k: spec.get(k) for k in
                          ("temp_id", "type", "title", "basis", "sources", "depends_on")},
                         ensure_ascii=False)
            + "\n来源原文：\n" + (fulltexts or "（无：basis=synthesis）"))
    return "\n\n".join(parts), allowed


def _fill_many_attempt(client: LLMClient, *, doc_id: str, chapter_id: str, specs: list[dict],
                       anchors_by_id: dict[str, dict], model: str | None, config: dict,
                       by_id: dict[str, dict], budget: BudgetGuard | None,
                       task_id: str | None, hint: str = "") -> tuple[dict[str, dict], dict[str, str]]:
    """一次批量填充调用；返回 (通过的块, 被拒的块 temp_id→原因)。

    只做校验级拒绝（缺块/字段非法/引文编造），原因供整批纠正重试；
    LLM 调用层错误（配额/超时/限流）直接抛出，由 fill_many 按块隔离。
    """
    profile = config.get("_profile", "textbook")
    schema = build_fill_many_schema(profile)
    max_in = int(config.get("generate", {}).get("fill_max_in", 8000))
    blocks_text, allowed = _fill_many_parts(specs, anchors_by_id, by_id, 800)
    prompt = FILL_MANY_PROMPT.format(
        n=len(specs),
        blocks_fulltexts=blocks_text,
        type_guide=type_guide(profile),
        fill_many_schema=compact_schema(schema),
    ) + "\n" + language_instruction((config.get("generate") or {}).get("output_language"))
    if estimate_tokens(prompt) > max_in:
        # 超预算：先裁剪来源原文（保留锚点 id 与结构）——与 fill_one 的护栏一致
        blocks_text, allowed = _fill_many_parts(specs, anchors_by_id, by_id, 600)
        prompt = FILL_MANY_PROMPT.format(
            n=len(specs),
            blocks_fulltexts=blocks_text,
            type_guide=type_guide(profile),
            fill_many_schema=compact_schema(schema),
        ) + "\n" + language_instruction((config.get("generate") or {}).get("output_language"))
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}]
    if hint:
        messages.append({"role": "user", "content": hint})
    max_out = int(config.get("generate", {}).get("fill_max_out", 6000))
    resp = client.complete("fill", messages, schema=schema,
                           max_tokens=max_out * min(3, len(specs)),
                           temperature=config.get("generate", {}).get("temperature"),
                           model=model, doc_id=doc_id, chapter_id=chapter_id,
                           task_id=task_id, budget=budget)
    out: dict[str, dict] = {}
    rejected: dict[str, str] = {}
    try:
        payload = parse_json_output(resp.get("output") if resp.get("output") is not None
                                    else resp.get("raw_text", ""))
        fills = payload.get("fills") if isinstance(payload, dict) else None
        if not isinstance(fills, list):
            raise SchemaViolation("fills 不是数组")
        for i, spec in enumerate(specs):
            item = next((f for f in fills
                         if isinstance(f, dict) and f.get("temp_id") == spec["temp_id"]), None)
            if item is None and len(fills) == len(specs) and isinstance(fills[i], dict) \
                    and not fills[i].get("temp_id"):
                item = fills[i]     # 位置对齐兜底：模型偶发漏写 temp_id（不得整批回退单块）
            if item is None:
                rejected[spec["temp_id"]] = "未出现在批量输出中"
                continue
            try:
                validate_fill_payload(item, allowed_anchor_ids=set(allowed[spec["temp_id"]]),
                                      anchors_by_id=anchors_by_id, spec=spec)
            except SchemaViolation as exc:
                rejected[spec["temp_id"]] = str(exc.message)
                continue
            ok, latex_warnings = check_latex(item.get("content", ""))
            item.setdefault("_warnings", []).extend(latex_warnings)
            item["_status"] = "ok" if ok else "needs_review"
            item["_temp_id"] = spec["temp_id"]
            out[spec["temp_id"]] = item
    except SchemaViolation as exc:
        return {}, {s["temp_id"]: str(exc.message) for s in specs}
    return out, rejected


def fill_many(client: LLMClient, *, doc_id: str, chapter_id: str, specs: list[dict],
              anchors_by_id: dict[str, dict], anchors: list[dict], model: str | None,
              config: dict, by_id: dict[str, dict], budget: BudgetGuard | None = None,
              task_id: str | None = None) -> dict[str, dict]:
    """一次调用填充多个块（小块合批提速，调用数降为 1/N）。

    批量输出被拒时先做一轮「整批纠正」：校验原因随提示带回、N 块仍只花一次调用；
    纠正后仍失败的块才回退 fill_one 逐块兜底。整批被拒曾直接触发 N 次逐块返工
    （实测 8 块烧 18 次调用、约半数输出 tokens 作废），纠正轮把这种风暴压到 ≤2 次调用。
    合批只提吞吐，不牺牲单块质量；配额/调用层错误同样按块隔离返回失败结果。
    """
    out: dict[str, dict] = {}
    attempt_specs = list(specs)
    hint = ""
    for round_no in range(2):
        if not attempt_specs:
            break
        try:
            accepted, rejected = _fill_many_attempt(
                client, doc_id=doc_id, chapter_id=chapter_id, specs=attempt_specs,
                anchors_by_id=anchors_by_id, model=model, config=config, by_id=by_id,
                budget=budget, task_id=task_id, hint=hint)
        except TaskCancelled:
            raise
        except QuotaExhausted as exc:
            out.update({s["temp_id"]: _failed_result(s, [str(exc.message)]) for s in attempt_specs})
            return out
        except (LLMError, RetryableError) as exc:
            out.update({s["temp_id"]: _failed_result(s, [f"LLM 调用失败: {exc.message}"])
                        for s in attempt_specs})
            return out
        except PriceUnknown as exc:
            if exc.code != "BUDGET_EXCEEDED":
                raise             # 配置类价格问题 → 任务清晰失败
            out.update({s["temp_id"]: _failed_result(s, [exc.message]) for s in attempt_specs})
            return out
        out.update(accepted)
        if not rejected:
            break
        if round_no == 0 and len(rejected) >= 2:
            # 整批纠正（≥2 块才值得）：一次调用伺候全部被拒块，先于逐块兜底
            attempt_specs = [s for s in attempt_specs if s["temp_id"] in rejected]
            hint = ("上一轮输出校验失败，请逐块修正后重新输出这些块的 fills"
                    "（只输出下列修正块，temp_id 保持不变）：\n"
                    + "\n".join(f"- {tid}: {msg}" for tid, msg in rejected.items()))
            continue
        break
    # 仍未通过的块逐块兜底（fill_one 自带修复重试）
    for spec in specs:
        if spec["temp_id"] in out:
            continue
        out[spec["temp_id"]] = fill_one(
            client, doc_id=doc_id, chapter_id=chapter_id, spec=spec,
            anchors_by_id=anchors_by_id, anchors=anchors, model=model, config=config,
            related_titles=[by_id[d]["title"] for d in spec.get("depends_on", []) if d in by_id],
            budget=budget, task_id=task_id)
    return out


def fill_all(client: LLMClient, *, doc_id: str, chapter_id: str, outline_items: list[dict],
             anchors_by_id: dict[str, dict], anchors: list[dict], model: str | None,
             config: dict, concurrency: int = 2, budget: BudgetGuard | None = None,
             task_id: str | None = None,
             progress: Callable[[dict], None] | None = None,
             is_cancelled: Callable[[], bool] | None = None) -> dict[str, dict]:
    """按依赖连续调度并行填充；严格保证 depends_on 目标先于依赖者完成（5.3.1）。

    调度是连续的而非「按波次」：依赖一完成，后继块立即占坑开跑。
    旧实现每波建一个线程池、整波做满才放行下一波——一个慢块会把所有
    已就绪的后继拖进下一波，链式依赖章节的填充被无谓串行（提速回归点）。
    """
    by_id = {i["temp_id"]: dict(i) for i in outline_items}
    total = len(outline_items)
    filled: dict[str, dict] = {}      # 只由调度线程写入，无需加锁

    def run(spec: dict) -> dict:
        if is_cancelled and is_cancelled():
            raise TaskCancelled("任务已取消")
        related = [by_id[d]["title"] for d in spec.get("depends_on", []) if d in by_id]
        return fill_one(client, doc_id=doc_id, chapter_id=chapter_id, spec=spec,
                        anchors_by_id=anchors_by_id, anchors=anchors, model=model,
                        config=config, related_titles=related, budget=budget, task_id=task_id)

    def run_many(specs: list[dict]) -> dict[str, dict]:
        if is_cancelled and is_cancelled():
            raise TaskCancelled("任务已取消")
        return fill_many(client, doc_id=doc_id, chapter_id=chapter_id, specs=specs,
                         anchors_by_id=anchors_by_id, anchors=anchors, model=model,
                         config=config, by_id=by_id, budget=budget, task_id=task_id)

    pending: dict[str, dict] = {i["temp_id"]: dict(i) for i in outline_items}
    known = set(pending)
    # 失效依赖（指向不存在的 temp_id）不构成等待条件，否则永远等不到就绪
    deps = {tid: set(i.get("depends_on", [])) & known for tid, i in pending.items()}
    done_ids: set[str] = set()
    futures: dict = {}
    # 小块合批（generate.fill_batch，默认 1 = 逐块；>1 时 N 块一次调用，填充调用数降为 1/N）
    fill_batch = max(1, int(config.get("generate", {}).get("fill_batch", 1) or 1))

    with ThreadPoolExecutor(max_workers=max(1, int(concurrency))) as pool:
        def _submit(tids: list[str]) -> None:
            specs = [pending.pop(t) for t in tids]
            if len(specs) == 1:
                futures[pool.submit(run, specs[0])] = tids
            else:
                futures[pool.submit(run_many, specs)] = tids

        def submit_ready() -> None:
            chunk: list[str] = []
            for tid in [t for t in list(pending) if deps[t] <= done_ids]:
                chunk.append(tid)
                if len(chunk) >= fill_batch:
                    _submit(chunk)
                    chunk = []
            if chunk:
                _submit(chunk)

        submit_ready()
        while pending or futures:
            if not futures:
                # 环形依赖兜底：剩余块按序逐个放行（与旧实现语义一致）
                _submit([next(iter(pending))])
            finished, _ = wait(set(futures), return_when=FIRST_COMPLETED)
            for fut in finished:
                tids = futures.pop(fut)
                res = fut.result()   # run 返回单块载荷 / run_many 返回 temp_id→载荷；取消/异常在此冒泡
                if isinstance(res, dict) and "_temp_id" in res:
                    filled[tids[0]] = res
                else:
                    filled.update(res)
                done_ids.update(tids)
                if progress:
                    progress({"stage": "fill", "done": len(filled), "total": total})
            submit_ready()
    return filled
