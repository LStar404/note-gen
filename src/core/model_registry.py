"""用户级模型注册表 data/models.json（9.5.1 / D21）：供应商 + 模型 + 能力 + 价格。

密钥二选一：api_key_env（环境变量名）或 api_key（本地明文）；API 响应永不回显完整密钥。
"""

from __future__ import annotations

import os
from typing import Any

from .errors import InputError, NotFound, PriceUnknown
from .jsonl import atomic_write_bytes
from .paths import Layout

DEFAULT_REGISTRY: dict[str, Any] = {
    "providers": [
        {
            "id": "openai",
            "label": "OpenAI",
            "protocol": "openai_compat",
            "base_url": "https://api.openai.com/v1",
            "api_key_env": "OPENAI_API_KEY",
            "api_key": None,
            "capabilities": {"json_schema": True, "tools": True, "vision": True},
            "models": [
                {
                    "id": "gpt-4o-mini",
                    "context_window": 128000,
                    "max_output_tokens": 16384,
                    "capabilities": {"json_schema": True, "tools": True, "vision": True},
                    "pricing": None,
                }
            ],
        }
    ]
}

CAP_FLAGS = ("json_schema", "tools", "vision")


def mask_key(key: str | None) -> str | None:
    if not key:
        return None
    return ("*" * max(0, len(key) - 4)) + key[-4:]


class ModelRegistry:
    def __init__(self, layout: Layout, environ: dict | None = None):
        self.layout = layout
        self.environ = environ if environ is not None else dict(os.environ)

    def path(self):
        return self.layout.models_registry

    def load(self) -> dict:
        import json

        p = self.path()
        if not p.exists():
            self.save(DEFAULT_REGISTRY)
            return json.loads(json.dumps(DEFAULT_REGISTRY))
        return json.loads(p.read_text(encoding="utf-8"))

    def save(self, data: dict) -> None:
        import json

        atomic_write_bytes(self.path(), (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))

    # ---- 查询 ----
    def providers(self) -> list[dict]:
        return list(self.load().get("providers", []))

    def provider(self, provider_id: str) -> dict:
        for p in self.providers():
            if p.get("id") == provider_id:
                return p
        raise NotFound(f"供应商不存在: {provider_id}")

    def find_model(self, model_name: str) -> tuple[dict, dict]:
        """模型名解析：`provider/model` 全名或全局唯一短名（9.5.1）。"""
        name = (model_name or "").strip()
        if not name:
            raise InputError("模型名为空")
        if "/" in name:
            pid, mid = name.split("/", 1)
            p = self.provider(pid)
            for m in p.get("models", []):
                if m.get("id") == mid:
                    return p, m
            raise NotFound(f"模型不存在: {model_name}")
        hits = []
        for p in self.providers():
            for m in p.get("models", []):
                if m.get("id") == name:
                    hits.append((p, m))
        if len(hits) == 1:
            return hits[0]
        if not hits:
            raise NotFound(f"模型不存在: {model_name}")
        raise InputError(f"模型名有歧义，请使用 provider/model 全名: {model_name}")

    def api_key(self, provider: dict) -> str | None:
        if provider.get("api_key_env"):
            val = self.environ.get(provider["api_key_env"])
            if val:
                return val
        return provider.get("api_key") or None

    def capabilities(self, model_name: str) -> dict:
        provider, model = self.find_model(model_name)
        caps = dict(provider.get("capabilities", {}))
        caps.update(model.get("capabilities", {}) or {})
        return {k: bool(caps.get(k, False)) for k in CAP_FLAGS}

    def limits(self, model_name: str) -> dict:
        _, model = self.find_model(model_name)
        return {
            "context_window": int(model.get("context_window") or 8192),
            "max_output_tokens": int(model.get("max_output_tokens") or 2048),
        }

    def pricing(self, model_name: str) -> dict | None:
        _, model = self.find_model(model_name)
        return model.get("pricing")

    def estimate_cost(self, model_name: str, tokens_in: int, tokens_out: int) -> float | None:
        pricing = self.pricing(model_name)
        if not pricing:
            return None
        try:
            cin = float(pricing.get("input_per_million", 0.0))
            cout = float(pricing.get("output_per_million", 0.0))
        except (TypeError, ValueError):
            return None
        return (tokens_in * cin + tokens_out * cout) / 1_000_000.0

    def require_priced(self, model_name: str, budget: float) -> None:
        """启用非零预算时拒绝未知价格模型（PRICE_UNKNOWN，5.3.5）。"""
        if budget and budget > 0 and self.pricing(model_name) is None:
            raise PriceUnknown(f"模型 {model_name} 价格未知，无法在非零预算下使用")

    # ---- 管理 ----
    def masked_provider(self, provider: dict) -> dict:
        out = json_safe(provider)
        out["api_key"] = mask_key(self.api_key(provider))
        out["api_key_env"] = provider.get("api_key_env")
        return out

    def upsert_provider(self, provider: dict) -> dict:
        pid = provider.get("id")
        if not pid:
            raise InputError("provider.id 必填")
        data = self.load()
        providers = [p for p in data.get("providers", []) if p.get("id") != pid]
        providers.append(provider)
        data["providers"] = providers
        self.save(data)
        return provider

    def delete_provider(self, provider_id: str, routed_models: list[str]) -> None:
        data = self.load()
        provider = self.provider(provider_id)
        names = {f"{provider_id}/{m.get('id')}" for m in provider.get("models", [])}
        names |= {m.get("id") for m in provider.get("models", [])}
        refs = [r for r in routed_models if r and (r in names or r.split("/")[0] == provider_id)]
        if refs:
            raise InputError("该供应商仍被任务路由引用", detail={"routes": refs})
        data["providers"] = [p for p in data.get("providers", []) if p.get("id") != provider_id]
        self.save(data)


def json_safe(obj: Any) -> Any:
    import json

    return json.loads(json.dumps(obj, ensure_ascii=False))
