"""生成缓存（5.3.4）：缓存键含 prompt_version 与实际路由模型指纹；命中只省 LLM 调用。"""

from __future__ import annotations

import hashlib
import json

from src.core.jsonl import atomic_write_bytes, canonical_json
from src.core.model_registry import ModelRegistry
from src.core.paths import Layout

from .prompts import PROMPT_VERSION

VALIDATOR_VERSION = "1"


def model_fingerprint(registry: ModelRegistry, model_name: str | None) -> dict:
    """缓存键取任务实际路由到的模型名；端点/能力/上下文变化必须失效。"""
    if not model_name:
        return {"model": None}
    try:
        provider, model = registry.find_model(model_name)
        caps = registry.capabilities(model_name)
        limits = registry.limits(model_name)
        return {
            "model": model_name,
            "provider_id": provider.get("id"),
            "protocol": provider.get("protocol"),
            "base_url": provider.get("base_url"),
            "capabilities": caps,
            "context_window": limits["context_window"],
            "max_output_tokens": limits["max_output_tokens"],
            "pricing": registry.pricing(model_name),
        }
    except Exception as exc:
        return {"model": model_name, "error": str(exc)}


def cache_key(*, doc_id: str, chapter_id: str, outline_hash: str, profile: str,
              registry_version: str, outline_model: dict, fill_model: dict,
              params: dict) -> str:
    obj = {
        "doc_id": doc_id,
        "chapter_id": chapter_id,
        "outline_hash": outline_hash,
        "profile": profile,
        "registry_version": registry_version,
        "prompt_version": PROMPT_VERSION,
        "validator_version": VALIDATOR_VERSION,
        "outline_model": outline_model,
        "fill_model": fill_model,
        "params": params,
    }
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def read_cache(layout: Layout, doc_id: str, chapter_id: str, key: str) -> dict | None:
    path = layout.cache_file(doc_id, chapter_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if data.get("cache_key") == key else None


def write_cache(layout: Layout, doc_id: str, chapter_id: str, key: str, payload: dict) -> None:
    """缓存存未分配最终 block_id 的候选集 N（含 temp_id 与依赖）。"""
    path = layout.cache_file(doc_id, chapter_id)
    body = {
        "cache_key": key,
        "prompt_version": PROMPT_VERSION,
        "candidates": payload.get("candidates", []),
        "outline_complete": payload.get("outline_complete", True),
        "meta": payload.get("meta", {}),
    }
    atomic_write_bytes(path, (json.dumps(body, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
