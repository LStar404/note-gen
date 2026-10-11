"""LLM 接入层（9.5.1）：LLMProvider 协议 + OpenAICompatAdapter + DummyProvider + 统一记账。

能力标志驱动行为：json_schema / tools / vision；代码不硬编码供应商名。
所有调用（含传输重试）统一写入 runtime.db.usage_log；调用前原子预留成本预算（5.3.5）。
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from typing import Any, Protocol

from .errors import AuthError, LLMError, NoteGenError, PriceUnknown, RetryableError
from .model_registry import ModelRegistry
from .text import estimate_tokens


class LLMProvider(Protocol):
    def complete(self, model: str, messages: list[dict], schema: dict | None,
                 max_tokens: int, temperature: float | None = None,
                 tools: list[dict] | None = None, tool_choice: str | dict | None = None,
                 json_schema: bool = True, context: dict | None = None,
                 reasoning_effort: str | None = None) -> dict: ...


def _json_mode_messages(messages: list[dict]) -> list[dict]:
    """json_object 模式要求提示里出现 "json" 字样（DeepSeek/Qwen 皆然），缺失时补一条系统指令。"""
    if "json" in json.dumps(messages, ensure_ascii=False).lower():
        return messages
    return [*messages, {"role": "system",
                        "content": "只输出一个合法的 json 对象：不要 markdown 代码围栏，不要任何解释。"}]


class OpenAICompatAdapter:
    """面向 Chat Completions 兼容端点（OpenAI/DeepSeek/Qwen/Kimi/GLM/Ollama/vLLM）。"""

    def __init__(self, provider_id: str, base_url: str, api_key: str | None = None,
                 timeout: float = 120.0):
        self.provider_id = provider_id
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def complete(self, model: str, messages: list[dict], schema: dict | None,
                 max_tokens: int, temperature: float | None = None,
                 tools: list[dict] | None = None, tool_choice: str | dict | None = None,
                 json_schema: bool = True, context: dict | None = None,
                 reasoning_effort: str | None = None) -> dict:
        import httpx

        body: dict[str, Any] = {"model": model, "messages": messages, "max_tokens": max_tokens}
        if temperature is not None:
            body["temperature"] = temperature
        if reasoning_effort:
            # 推理深度（MiMo 等推理模型实测支持 low/medium/high）：
            # 思考型模型单次调用的大头是思考 token，low 可显著缩短解析/填充耗时
            body["reasoning_effort"] = reasoning_effort
        if schema is not None:
            if json_schema:
                body["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": "response", "schema": schema, "strict": False},
                }
            else:
                # 能力标志 json_schema=false（如 DeepSeek）：上游对 json_schema 直接回 400
                # "This response_format type is unavailable now"，只能退回 json_object
                body["response_format"] = {"type": "json_object"}
                body["messages"] = _json_mode_messages(messages)
        if tools:
            body["tools"] = tools
        if tool_choice is not None:
            body["tool_choice"] = tool_choice
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request_id = "req_" + uuid.uuid4().hex[:16]
        try:
            resp = httpx.post(self.base_url + "/chat/completions", json=body, headers=headers,
                              timeout=self.timeout)
        except Exception as exc:  # 传输层失败按可重试处理（10.4）
            raise RetryableError(f"LLM 请求失败: {exc}")
        if resp.status_code in (401, 403):
            # 鉴权失败重试不可能成功：立即上抛，别让用户等满退避才看到含混提示
            raise AuthError(
                f"LLM 鉴权失败 {resp.status_code}：API Key 无效或无权访问模型 {model}"
                f"（供应商 {self.provider_id}）——请在「配置指南 → 接入模型」核对密钥与模型 id",
                detail={"provider_id": self.provider_id, "status": resp.status_code,
                        "body": resp.text[:500]})
        if resp.status_code in (429, 500, 502, 503, 504):
            raise RetryableError(f"LLM 服务返回 {resp.status_code}", detail={"body": resp.text[:500]})
        if resp.status_code >= 400:
            # 其余 4xx（400 参数/404 模型不存在/422 等）同样不可重试
            raise LLMError(
                f"LLM 调用被拒绝 {resp.status_code}（供应商 {self.provider_id}，模型 {model}）",
                detail={"provider_id": self.provider_id, "status": resp.status_code,
                        "body": resp.text[:500]})
        data = resp.json()
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        content = message.get("content")
        usage = data.get("usage") or {}
        tool_calls = message.get("tool_calls") or []
        output: Any = None
        if content:
            try:
                output = json_repair_loads(content)
            except Exception:
                output = content
        return {
            "output": output,
            "raw_text": content if isinstance(content, str) else None,
            "tool_calls": tool_calls,
            "finish_reason": choice.get("finish_reason", "stop"),
            "usage": {
                "in": usage.get("prompt_tokens"),
                "out": usage.get("completion_tokens"),
                "estimated": not bool(usage.get("prompt_tokens")),
            },
            "request_id": data.get("id") or request_id,
        }


class DummyProvider:
    """测试用固定输出；按调用顺序出队，队列为空时返回最后一项。"""

    def __init__(self, responses: list[Any] | None = None, provider_id: str = "dummy"):
        self.responses = list(responses or [])
        self.provider_id = provider_id
        self.calls: list[dict] = []

    def complete(self, model: str, messages: list[dict], schema: dict | None,
                 max_tokens: int, temperature: float | None = None,
                 tools: list[dict] | None = None, tool_choice: str | dict | None = None,
                 json_schema: bool = True, context: dict | None = None,
                 reasoning_effort: str | None = None) -> dict:
        self.calls.append({"model": model, "messages": messages, "tools": tools,
                           "tool_choice": tool_choice, "schema": schema,
                           "reasoning_effort": reasoning_effort})
        item = self.responses.pop(0) if self.responses else {}
        if isinstance(item, Exception):
            raise item
        if isinstance(item, dict) and ("output" in item or "tool_calls" in item):
            out = dict(item)
            out.setdefault("finish_reason", "stop")
            out.setdefault("tool_calls", [])
            out.setdefault("usage", {"in": 10, "out": 10, "estimated": True})
            out.setdefault("request_id", "dummy-" + uuid.uuid4().hex[:8])
            return out
        text = item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
        return {
            "output": item,
            "raw_text": text,
            "tool_calls": [],
            "finish_reason": "stop",
            "usage": {"in": estimate_tokens(str(messages)), "out": estimate_tokens(text), "estimated": True},
            "request_id": "dummy-" + uuid.uuid4().hex[:8],
        }


def json_repair_loads(text: str) -> Any:
    try:
        return json.loads(text)
    except Exception:
        pass
    try:
        from json_repair import repair_json

        return json.loads(repair_json(text))
    except Exception:
        return text


class BudgetGuard:
    """单章成本上限：调用前在共享预算锁内原子预留（5.3.5）。"""

    def __init__(self, limit_usd: float = 0.0):
        self.limit = float(limit_usd or 0.0)
        self._lock = threading.Lock()
        self._reserved = 0.0

    def reserve(self, cost_estimate: float | None) -> None:
        if self.limit <= 0:
            return
        if cost_estimate is None:
            raise PriceUnknown("成本未知，无法在非零预算下调用")
        with self._lock:
            if self._reserved + cost_estimate > self.limit:
                # code=BUDGET_EXCEEDED：运行期预算用尽，与配置类 PriceUnknown 区分——
                # 前者按块隔离保住已完成成果，后者（NO_MODEL/PRICE_UNKNOWN）必须冒泡
                raise PriceUnknown("超出单章成本上限 max_cost_per_chapter",
                                   code="BUDGET_EXCEEDED",
                                   detail={"limit": self.limit, "reserved": self._reserved})
            self._reserved += cost_estimate

    def settle(self, cost_estimate: float | None, reserved: float | None) -> None:
        pass  # 预留额即已用额：超预算停止新调用（不回滚已发生成本）


class LLMClient:
    """按任务路由分发到模型；统一记账、重试与预算。"""

    def __init__(self, registry: ModelRegistry, runtime, config: dict,
                 overrides: dict[str, LLMProvider] | None = None):
        self.registry = registry
        self.runtime = runtime
        self.config = config
        self.overrides = overrides or {}
        self._providers: dict[str, LLMProvider] = {}
        # 单次调用超时（5.3.2 可配置）：推理型模型（MiMo 等）单次可超 2 分钟，
        # 默认 120s 会被误判超时并白重试（实测 ch08 提纲阶段多次 success=0 空转）
        self.timeout = float((config.get("generate", {}) or {}).get("timeout", 600.0))
        # 推理深度（generate.reasoning_effort）：low/medium/high，空=供应商默认
        self.reasoning_effort = (config.get("generate", {}) or {}).get("reasoning_effort") or None
        # 全局在途请求总闸（generate.max_concurrent_requests，默认 8）：
        # 并行分章后「章数 × 章内并发」会一次性打出十几个并发请求，
        # 触发供应商限流反而被退避拖慢——按信号量把总并发钉在上限内
        self._slots = threading.BoundedSemaphore(
            max(1, int((config.get("generate", {}) or {}).get("max_concurrent_requests", 8) or 8)))

    # ---- 路由 ----
    def route(self, task: str) -> str:
        models = self.config.get("generate", {}).get("models", {}) or {}
        name = models.get(task) or models.get("default")
        if not name:
            raise PriceUnknown("未配置生成模型（config.yaml generate.models）", code="NO_MODEL")
        return name

    def _provider_for(self, model_name: str) -> tuple[dict, dict, LLMProvider]:
        provider, model = self.registry.find_model(model_name)
        pid = provider["id"]
        if pid in self.overrides:
            return provider, model, self.overrides[pid]
        if pid not in self._providers:
            self._providers[pid] = OpenAICompatAdapter(
                pid, provider.get("base_url", ""), self.registry.api_key(provider),
                timeout=self.timeout)
        return provider, model, self._providers[pid]

    # ---- 调用 ----
    def complete(self, task: str, messages: list[dict], *, schema: dict | None = None,
                 max_tokens: int = 2048, temperature: float | None = None,
                 tools: list[dict] | None = None, tool_choice: str | dict | None = None,
                 model: str | None = None, doc_id: str | None = None,
                 chapter_id: str | None = None, task_id: str | None = None,
                 budget: BudgetGuard | None = None) -> dict:
        model_name = model or self.route(task)
        provider_cfg, model_cfg, adapter = self._provider_for(model_name)
        # 路由名是 "provider/model"，供应商 API 只认裸模型 id
        # （原实现把全名原样上传，DeepSeek 直接 400：but you passed deepseek/deepseek-flash）
        api_model = (model_cfg or {}).get("id") or model_name
        caps = self.registry.capabilities(model_name)
        limits = self.registry.limits(model_name)
        max_tokens = int(min(max_tokens, limits["max_output_tokens"]))
        self.registry.require_priced(model_name, budget.limit if budget else 0.0)

        est_in = sum(estimate_tokens(str(m.get("content", ""))) for m in messages)
        est_out = max_tokens
        reserved_cost = self.registry.estimate_cost(model_name, est_in, est_out)
        if budget:
            budget.reserve(reserved_cost)

        attempts = 0
        while True:
            attempts += 1
            request_id = "req_" + uuid.uuid4().hex[:16]
            try:
                with self._slots:
                    result = adapter.complete(api_model, messages, schema, max_tokens,
                                              temperature=temperature, tools=tools,
                                              tool_choice=tool_choice,
                                              json_schema=bool(caps.get("json_schema")),
                                              context={"task": task, "doc_id": doc_id, "chapter_id": chapter_id},
                                              reasoning_effort=self.reasoning_effort)
            except RetryableError:
                self.runtime.log_usage(
                    provider_id=provider_cfg["id"], model=model_name, task=task,
                    request_id=request_id, doc_id=doc_id, chapter_id=chapter_id,
                    tokens_in=est_in, tokens_out=None, cost_est=None,
                    success=False, estimated=True, task_id=task_id)
                if attempts >= 3:
                    raise
                time.sleep(min(8.0, 0.5 * (2 ** attempts)))
                continue
            except NoteGenError:
                # 不可重试错误（鉴权/参数/模型不存在）：只记一次失败，立即上抛
                self.runtime.log_usage(
                    provider_id=provider_cfg["id"], model=model_name, task=task,
                    request_id=request_id, doc_id=doc_id, chapter_id=chapter_id,
                    tokens_in=est_in, tokens_out=None, cost_est=None,
                    success=False, estimated=True, task_id=task_id)
                raise

            usage = result.get("usage") or {}
            tin = usage.get("in") or est_in
            tout = usage.get("out") or est_out
            cost = self.registry.estimate_cost(model_name, tin, tout)
            self.runtime.log_usage(
                provider_id=provider_cfg["id"], model=model_name, task=task,
                request_id=result.get("request_id") or request_id, doc_id=doc_id,
                chapter_id=chapter_id, tokens_in=tin, tokens_out=tout,
                cost_est=cost, success=True, estimated=bool(usage.get("estimated")),
                task_id=task_id)
            result["model"] = model_name
            result["provider_id"] = provider_cfg["id"]
            result["usage"]["in"] = tin
            result["usage"]["out"] = tout
            return result

    def test_connection(self, provider_id: str, model_name: str) -> dict:
        """最小调用控成本；区分连通性、模型权限与能力（10.6.6）。"""
        provider = self.registry.provider(provider_id)
        full = f"{provider_id}/{model_name}"
        self.registry.find_model(full)
        adapter = self.overrides.get(provider_id) or OpenAICompatAdapter(
            provider_id, provider.get("base_url", ""), self.registry.api_key(provider), timeout=30.0)
        request_id = "req_" + uuid.uuid4().hex[:16]
        try:
            result = adapter.complete(model_name, [{"role": "user", "content": "ping"}], None, 8)
        except Exception as exc:
            self.runtime.log_usage(provider_id=provider_id, model=model_name, task="test",
                                   request_id=request_id, success=False)
            return {"ok": False, "error": str(exc)}
        self.runtime.log_usage(provider_id=provider_id, model=model_name, task="test",
                               request_id=result.get("request_id") or request_id, success=True,
                               tokens_in=(result.get("usage") or {}).get("in"),
                               tokens_out=(result.get("usage") or {}).get("out"),
                               estimated=bool((result.get("usage") or {}).get("estimated")))
        caps = self.registry.capabilities(full)
        return {"ok": True, "usage": result.get("usage"), "capabilities": caps,
                "note": "连通成功不代表 json_schema/tools 均可用，能力以显式配置为准"}
