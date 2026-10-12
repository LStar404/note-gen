"""运行配置 config.yaml 加载（10.2：字段即规范）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

DEFAULTS: dict[str, Any] = {
    "app": {
        "host": "127.0.0.1",
        "port": 8787,
        "cors_origins": ["http://127.0.0.1:5173"],
    },
    "parser": {"default": "marker", "ocr_mode": "auto", "python": None},
    "chapters_override": [],
    "generate": {
        "models": {
            "default": None,
            "outline": None,
            "fill": None,
            "agent": None,
            "link": None,
        },
        "outline_max_in": 32000,
        "fill_max_in": 8000,
        "outline_max_out": 32000,
        "fill_max_out": 12000,
        "concurrency": 2,
        "timeout": 600,          # LLM 单次调用超时秒数（推理型模型需更长）
        "reasoning_effort": None,  # 推理深度 low/medium/high（空=供应商默认；low 显著提速）
        "thinking": None,          # 思考开关 disabled/enabled（deepseek-flash 实测 disabled 提速 5 倍）
        "fill_batch": 1,           # 小块合批：N 块一次填充调用（1=逐块）
        "outline_batch_out": 8000, # 提纲分批目标（预计输出 token/批）
        "chapter_concurrency": 2,  # 整本生成并行章数
        "max_concurrent_requests": 8,  # 全局在途 LLM 请求上限
        "max_cost_per_chapter": 0.0,
        "temperature": 0.2,
        "output_language": "zh",      # zh | en | ja  生成笔记与导出标签语言
    },
    "export": {"encoding": "utf-8"},
    "embedding": {"provider": "none", "model_dir": None, "dim": 1024, "threshold": 0.55},
    "cache": {"dir": "cache/gen"},
    "match": {"contain_ratio": 0.8, "cover_ratio": 0.5, "top_k": 8},
    "history": {"keep_recent": 50},
}


ENCODINGS = ("utf-8", "utf-8-sig", "gbk", "big5")
OUTPUT_LANGUAGES = ("zh", "en", "ja")
REASONING_EFFORTS = ("low", "medium", "high")
THINKING_MODES = ("disabled", "enabled")


def normalize_settings(cfg: dict) -> dict:
    """校验并归一化可选项（10.2 字段即规范）。"""
    enc = ((cfg.get("export") or {}).get("encoding") or "utf-8").lower()
    cfg.setdefault("export", {})["encoding"] = enc if enc in ENCODINGS else "utf-8"
    lang = ((cfg.get("generate") or {}).get("output_language") or "zh").lower()
    cfg.setdefault("generate", {})["output_language"] = lang if lang in OUTPUT_LANGUAGES else "zh"
    # 推理深度：仅接受 low/medium/high，空值=供应商默认（不限制思考长度）
    effort = ((cfg.get("generate") or {}).get("reasoning_effort") or "").lower() or None
    cfg["generate"]["reasoning_effort"] = effort if effort in REASONING_EFFORTS else None
    # 思考开关：拼错的值不得静默放行（否则悄悄回到昂贵的思考默认）
    think = ((cfg.get("generate") or {}).get("thinking") or "").lower() or None
    cfg["generate"]["thinking"] = think if think in THINKING_MODES else None
    return cfg


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(root: str | Path) -> dict:
    path = Path(root) / "config.yaml"
    data: dict = {}
    if path.exists():
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise ValueError(f"{path} 顶层必须是映射")
        data = loaded
    cfg = _deep_merge(DEFAULTS, data)
    cfg = normalize_settings(cfg)
    cfg["_root"] = str(Path(root).resolve())
    return cfg


def save_config(root: str | Path, cfg: dict) -> None:
    """原子保存 config.yaml（模型路由更新用）；不含密钥。"""
    out = {k: v for k, v in cfg.items() if not k.startswith("_")}
    path = Path(root) / "config.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    from .jsonl import atomic_write_bytes

    atomic_write_bytes(path, (yaml.safe_dump(out, allow_unicode=True, sort_keys=False)).encode("utf-8"))
