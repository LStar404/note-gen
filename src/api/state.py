"""应用状态装配：布局/配置/运行时库/模型注册表/LLM 客户端/任务管理。"""

from __future__ import annotations

from pathlib import Path

from src.core.config import load_config, save_config
from src.core.llm import LLMClient
from src.core.model_registry import ModelRegistry
from src.core.paths import Layout
from src.db.runtime import RuntimeDB


class AppState:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.layout = Layout(self.root).ensure_base()
        self.config = load_config(self.root)
        self.runtime = RuntimeDB(self.layout.runtime_db)
        self.registry = ModelRegistry(self.layout)
        self.client = LLMClient(self.registry, self.runtime, self.config)
        self.token = (self.layout.data_dir / ".local_token")
        from .security import load_or_create_token

        self.token_value = load_or_create_token(self.token)
        from .tasks import TaskManager

        self.tasks = TaskManager(self.runtime)
        self.tasks.interrupt_stale()

    def reload_config(self) -> dict:
        self.config = load_config(self.root)
        self.client.config = self.config
        return self.config

    def save_model_routes(self, routes: dict) -> dict:
        cfg = dict(self.config)
        gen = dict(cfg.get("generate", {}))
        models = dict(gen.get("models", {}))
        for slot in ("default", "outline", "fill", "agent", "link"):
            if slot in routes:
                models[slot] = routes[slot]
        gen["models"] = models
        cfg["generate"] = gen
        save_config(self.root, cfg)
        return self.reload_config()

    def routed_models(self) -> list[str]:
        return [v for v in (self.config.get("generate", {}).get("models", {}) or {}).values() if v]
