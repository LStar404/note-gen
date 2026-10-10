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
        "concurrency": 2,
        "max_cost_per_chapter": 0.0,
        "temperature": 0.2,
    },
    "embedding": {"provider": "none", "model_dir": None, "dim": 1024, "threshold": 0.55},
    "cache": {"dir": "cache/gen"},
    "match": {"contain_ratio": 0.8, "cover_ratio": 0.5, "top_k": 8},
    "history": {"keep_recent": 50},
}


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
    cfg["_root"] = str(Path(root).resolve())
    return cfg


def save_config(root: str | Path, cfg: dict) -> None:
    """原子保存 config.yaml（模型路由更新用）；不含密钥。"""
    out = {k: v for k, v in cfg.items() if not k.startswith("_")}
    path = Path(root) / "config.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    from .jsonl import atomic_write_bytes

    atomic_write_bytes(path, (yaml.safe_dump(out, allow_unicode=True, sort_keys=False)).encode("utf-8"))
