"""第二次生成：逐块填充（5.3.1/5.3.2）；依赖目标先完成，批大小 = concurrency。"""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from src.core.errors import SchemaViolation
from src.core.llm import BudgetGuard, LLMClient
from src.core.text import estimate_tokens

from .prompts import FILL_PROMPT, SYSTEM_PROMPT, build_fill_schema, language_instruction
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
    fulltexts = "\n\n".join(f"[{aid}] {anchors_by_id[aid].get('content', '')[:2000]}" for aid in source_ids)
    adj = _adjacent_anchors(anchors, source_ids)
    if adj:
        fulltexts += "\n\n相邻锚点（仅供理解语境，不得作为来源）：\n" + "\n".join(
            f"[{a['anchor_id']}] {a.get('content', '')[:200]}" for a in adj)
    if related_titles:
        fulltexts += "\n\n相关已生成块标题：" + "；".join(related_titles[:8])

    fig_id = next((s["anchor_id"] for s in spec.get("sources", []) if s.get("figure_id")), "fig_00000000")
    prompt = FILL_PROMPT.format(
        block_type=spec.get("type", "definition"),
        title=spec.get("title", ""),
        fig_id=fig_id,
        block_spec_json=json.dumps({k: spec.get(k) for k in
                                    ("temp_id", "type", "title", "basis", "sources", "depends_on")},
                                   ensure_ascii=False),
        source_fulltexts=fulltexts,
        fill_schema=json.dumps(schema, ensure_ascii=False),
    ) + "\n" + language_instruction((config.get("generate") or {}).get("output_language"))
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}]
    max_in = int(config.get("generate", {}).get("fill_max_in", 8000))
    if estimate_tokens(prompt) > max_in:
        fulltexts = "\n\n".join(f"[{aid}] {anchors_by_id[aid].get('content', '')[:800]}" for aid in source_ids)
        prompt = prompt.replace(fulltexts, fulltexts)
        messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}]

    errors: list[str] = []
    for attempt in range(4):           # 首次 + 最多 3 次修复
        resp = client.complete("fill", messages, schema=schema, max_tokens=2000,
                               temperature=config.get("generate", {}).get("temperature"),
                               model=model, doc_id=doc_id, chapter_id=chapter_id,
                               task_id=task_id, budget=budget)
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
            messages = messages[:2] + [
                {"role": "assistant", "content": json.dumps(resp.get("output"), ensure_ascii=False)[:4000]},
                {"role": "user", "content": f"上一次输出校验失败：{exc.message}。请修正后重新输出 JSON。"},
            ]
    return {"_temp_id": spec["temp_id"], "_status": "failed", "_errors": errors,
            "content": "", "content_format": "md_latex", "tags": [],
            "sources_patch": spec.get("sources", []), "basis": spec.get("basis", "anchored")}


def fill_all(client: LLMClient, *, doc_id: str, chapter_id: str, outline_items: list[dict],
             anchors_by_id: dict[str, dict], anchors: list[dict], model: str | None,
             config: dict, concurrency: int = 2, budget: BudgetGuard | None = None,
             task_id: str | None = None) -> dict[str, dict]:
    """按依赖分批并行填充；严格保证 depends_on 目标先于依赖者完成（5.3.1）。"""
    by_id = {i["temp_id"]: dict(i) for i in outline_items}
    filled: dict[str, dict] = {}
    semaphore = threading.Semaphore(max(1, int(concurrency)))
    lock = threading.Lock()

    def run(spec: dict) -> tuple[str, dict]:
        with semaphore:
            related = [by_id[d]["title"] for d in spec.get("depends_on", []) if d in by_id]
            out = fill_one(client, doc_id=doc_id, chapter_id=chapter_id, spec=spec,
                           anchors_by_id=anchors_by_id, anchors=anchors, model=model,
                           config=config, related_titles=related, budget=budget, task_id=task_id)
        return spec["temp_id"], out

    pending = [dict(i) for i in outline_items]
    while pending:
        ready = [i for i in pending if all(d in filled for d in i.get("depends_on", []))]
        if not ready:
            # 环形/失效依赖：剩余块置后重排，逐个填充
            ready = pending[:1]
        with ThreadPoolExecutor(max_workers=max(1, int(concurrency))) as pool:
            futures = [pool.submit(run, spec) for spec in ready]
            for fut in as_completed(futures):
                tid, out = fut.result()
                with lock:
                    filled[tid] = out
        ready_ids = {i["temp_id"] for i in ready}
        pending = [i for i in pending if i["temp_id"] not in ready_ids]
    return filled
