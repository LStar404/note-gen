"""Agent 编辑两步式（5.5 / 9.4）：plan → 用户确认 → exec；服务端强制 scope。"""

from __future__ import annotations

import json
import uuid

from src.core.errors import ConflictError, InputError, NotFound, SchemaViolation
from src.core.store import DocStore
from src.generate.prompts import AGENT_SYSTEM_PROMPT

AGENT_TOOLS = [
    {"type": "function", "function": {
        "name": "update_block",
        "description": "更新一个块的内容/标题/来源",
        "parameters": {"type": "object", "required": ["block_id", "message"],
                       "properties": {"block_id": {"type": "string"},
                                      "title": {"type": "string"},
                                      "content": {"type": "string"},
                                      "sources": {"type": "array"},
                                      "message": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "create_block",
        "description": "在目标章内新建一个块",
        "parameters": {"type": "object", "required": ["chapter_id", "type", "title", "content", "message"],
                       "properties": {"chapter_id": {"type": "string"},
                                      "type": {"type": "string"},
                                      "title": {"type": "string"},
                                      "content": {"type": "string"},
                                      "sources": {"type": "array"},
                                      "message": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "delete_block",
        "description": "软删除一个块",
        "parameters": {"type": "object", "required": ["block_id", "message"],
                       "properties": {"block_id": {"type": "string"}, "message": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "link_blocks",
        "description": "建立从 from_block_id 到 to_block_id 的单向链接",
        "parameters": {"type": "object", "required": ["from_block_id", "to_block_id", "message"],
                       "properties": {"from_block_id": {"type": "string"},
                                      "to_block_id": {"type": "string"},
                                      "message": {"type": "string"}}}}},
]


def _scope(store: DocStore, *, block_id: str | None = None, chapter_id: str | None = None) -> dict:
    if block_id:
        block = store.get_block(block_id)
        return {"kind": "block", "block_id": block_id, "chapter_id": block["chapter_id"]}
    if chapter_id:
        store.chapter(chapter_id)
        return {"kind": "chapter", "block_id": None, "chapter_id": chapter_id}
    raise InputError("必须指定 block_id 或 chapter_id")


def plan_edit(state, *, doc_id: str, instruction: str, block_id: str | None = None,
              chapter_id: str | None = None) -> dict:
    """步骤 1：生成 plan（不执行）；参数在此阶段生成并校验（9.4）。"""
    store = DocStore(state.layout, doc_id)
    scope = _scope(store, block_id=block_id, chapter_id=chapter_id)
    anchors_by_id = store.anchors_by_id()

    if scope["kind"] == "block":
        targets = [store.get_block(block_id)]
    else:
        targets = store.block_rows(scope["chapter_id"])[:20]

    source_lines = []
    for b in targets:
        source_lines.append(f"[block {b['id']}] ({b['type']}) {b['title']}\n{b['content'][:800]}")
        for src in b.get("sources", [])[:3]:
            anchor = anchors_by_id.get(src["anchor_id"], {})
            source_lines.append(f"  [source {src['anchor_id']}] {anchor.get('content', '')[:300]}")
    payload = ("<source_data>\n用户指令：%s\n%s\n</source_data>" %
               (instruction, "\n".join(source_lines)))

    messages = [{"role": "system", "content": AGENT_SYSTEM_PROMPT},
                {"role": "user", "content": payload}]
    client = state.client
    caps = {}
    try:
        caps = state.registry.capabilities(client.route("agent"))
    except Exception:
        caps = {}
    if not caps.get("tools"):
        raise InputError("agent 编辑需要具备 tools 能力的模型，请在设置中切换", code="TOOLS_REQUIRED")

    resp = client.complete("agent", messages, schema=None, max_tokens=2000,
                           tools=AGENT_TOOLS, tool_choice="auto", doc_id=doc_id)
    tool_calls = _extract_tool_calls(resp)
    proposed = []
    affected_revisions = {}
    for call in tool_calls:
        args = call.get("args", {})
        check_scope(store, scope, call["name"], args)
        if call["name"] in ("update_block", "delete_block", "link_blocks"):
            target = args.get("block_id") or args.get("from_block_id")
            if target:
                affected_revisions[target] = store.get_block(target)["revision"]
        proposed.append({"tool": call["name"], "args": args, "message": args.get("message", "")})

    plan_id = "p_" + uuid.uuid4().hex[:12]
    plan = {
        "plan_id": plan_id,
        "plan": {
            "intent": instruction,
            "affected_blocks": sorted(affected_revisions),
            "proposed_changes": proposed,
        },
    }
    state.runtime.save_session(plan_id, doc_id, f"{scope['kind']}:{scope['block_id'] or scope['chapter_id']}",
                               [{"role": "user", "content": instruction},
                                {"role": "assistant", "content": json.dumps(proposed, ensure_ascii=False)}],
                               "plan", confirmed_calls=proposed, affected_revisions=affected_revisions)
    return plan


def execute_plan(state, *, doc_id: str, plan_id: str, confirmed: bool = False) -> dict:
    """步骤 2：执行已确认的工具参数；每个工具一个可恢复提交，operation_id 去重。

    exec 先校验 affected_revisions（计划时快照），再逐条执行；期间被改动则中止剩余工具。
    """
    session = state.runtime.get_session(plan_id)
    if session is None:
        raise NotFound(f"plan 不存在: {plan_id}")
    if not confirmed:
        raise InputError("exec 需要 confirmed=true")
    snapshot = {k: int(v) for k, v in (session.get("affected_revisions") or {}).items()}
    store = DocStore(state.layout, doc_id)
    scope_kind, scope_target = session["scope"].split(":", 1)
    scope = {"kind": scope_kind, "block_id": scope_target if scope_kind == "block" else None,
             "chapter_id": scope_target if scope_kind == "chapter" else store.get_block(scope_target)["chapter_id"]}

    applied, errors = [], []
    for i, call in enumerate(session.get("confirmed_calls", [])):
        operation_id = f"{plan_id}:{i}"
        done = state.runtime.applied_operation(operation_id)
        if done:
            applied.append(done.get("result_json", {}))
            continue
        args = dict(call.get("args", {}))
        try:
            check_scope(store, scope, call["tool"], args)
            # 计划快照校验：目标块在 plan 之后被改过 → 中止，防止覆盖并发改动
            for target_id in _targets_of(call["tool"], args):
                if target_id in snapshot:
                    current = store.get_block(target_id)["revision"]
                    if int(current) != snapshot[target_id]:
                        raise ConflictError(
                            f"块 {target_id} 在计划确认后被修改（{snapshot[target_id]} → {current}），"
                            f"已中止剩余操作，请重新规划",
                            code="PLAN_STALE")
            unlocked = (state.runtime.is_unlocked(args.get("block_id", ""), plan_id)
                        or state.runtime.is_unlocked(args.get("from_block_id", ""), plan_id))
            result = run_tool(store, call["tool"], args, author="agent", plan_id=plan_id,
                              unlocked_for_agent=unlocked)
        except Exception as exc:
            # 部分执行语义：中止剩余工具，已成功部分保留；错误带稳定 code 供前端处理
            errors.append({"tool": call.get("tool"),
                           "code": getattr(exc, "code", "INTERNAL_ERROR"),
                           "error": str(exc)})
            break
        state.runtime.record_operation(operation_id, plan_id, result)
        applied.append(result)
    state.runtime.save_session(plan_id, doc_id, session["scope"], session["messages"], "done",
                               confirmed_calls=session.get("confirmed_calls", []),
                               affected_revisions=session.get("affected_revisions", {}))
    out = {"plan_id": plan_id, "diff": {"applied": applied}, "partial": bool(errors)}
    if errors:
        out["error"] = errors[0]
    return out


def _targets_of(tool: str, args: dict) -> list[str]:
    if tool in ("update_block", "delete_block"):
        return [args.get("block_id")] if args.get("block_id") else []
    if tool == "link_blocks":
        return [args.get("from_block_id")] if args.get("from_block_id") else []
    return []


def check_scope(store: DocStore, scope: dict, tool: str, args: dict) -> None:
    """服务端强制 scope（9.4）：越界调用一律拒绝。"""
    chapter = scope["chapter_id"]
    if tool == "update_block":
        bid = args.get("block_id")
        block = store.get_block(bid)
        if scope["kind"] == "block" and bid != scope["block_id"]:
            raise SchemaViolation("update_block 越界：只能修改目标块", code="SCOPE_VIOLATION")
        if block["chapter_id"] != chapter:
            raise SchemaViolation("update_block 越界：跨章", code="SCOPE_VIOLATION")
    elif tool == "create_block":
        if args.get("chapter_id") != chapter:
            raise SchemaViolation("create_block 越界：只能在目标章创建", code="SCOPE_VIOLATION")
    elif tool == "delete_block":
        bid = args.get("block_id")
        block = store.get_block(bid)
        if scope["kind"] == "block" and bid != scope["block_id"]:
            raise SchemaViolation("delete_block 越界：只能删除目标块", code="SCOPE_VIOLATION")
        if block["chapter_id"] != chapter:
            raise SchemaViolation("delete_block 越界：跨章", code="SCOPE_VIOLATION")
    elif tool == "link_blocks":
        src = args.get("from_block_id")
        dst = args.get("to_block_id")
        if scope["kind"] == "block" and src != scope["block_id"]:
            raise SchemaViolation("link_blocks 越界：from_block_id 必须是目标块", code="SCOPE_VIOLATION")
        store.get_block(src)
        store.get_block(dst)
    else:
        raise SchemaViolation(f"未知工具: {tool}")


def run_tool(store: DocStore, tool: str, args: dict, *, author: str = "agent",
             plan_id: str | None = None, unlocked_for_agent: bool = False) -> dict:
    if tool == "update_block":
        block = store.update_block(args["block_id"],
                                   {k: v for k, v in args.items() if k in
                                    ("title", "content", "sources")},
                                   expected_revision=None, author=author,
                                   unlocked_for_agent=unlocked_for_agent,
                                   message=args.get("message", "agent 编辑"))
        return {"tool": tool, "block_id": block["id"], "revision": block["revision"]}
    if tool == "create_block":
        block = store.create_block({"type": args["type"], "title": args["title"],
                                    "content": args["content"],
                                    "sources": args.get("sources", []),
                                    "order": 0.0},
                                   author=author, message=args.get("message", "agent 新建"),
                                   chapter_id=args["chapter_id"])
        return {"tool": tool, "block_id": block["id"], "revision": block["revision"]}
    if tool == "delete_block":
        block = store.soft_delete_block(args["block_id"], author=author,
                                        message=args.get("message", "agent 删除"))
        return {"tool": tool, "block_id": block["id"], "revision": block["revision"]}
    if tool == "link_blocks":
        from_block = store.get_block(args["from_block_id"])
        links = list(from_block.get("links", []))
        if args["to_block_id"] not in links:
            links.append(args["to_block_id"])
        block = store.update_block(args["from_block_id"], {"links": links},
                                   expected_revision=None, author=author,
                                   message=args.get("message", "agent 链接"))
        return {"tool": tool, "block_id": block["id"], "revision": block["revision"]}
    raise InputError(f"未知工具: {tool}")


def _extract_tool_calls(resp: dict) -> list[dict]:
    out = []
    for call in resp.get("tool_calls") or []:
        fn = call.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except Exception:
            args = {}
        out.append({"name": fn.get("name"), "args": args})
    if not out and isinstance(resp.get("output"), dict) and "tool_calls" in resp["output"]:
        for call in resp["output"]["tool_calls"]:
            out.append({"name": call.get("name"), "args": call.get("args", {})})
    return out
