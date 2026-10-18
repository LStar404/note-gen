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
        # 自定义块类型注入类型注册表（生成 schema/提示词/校验器/前端同一事实源）
        from src.core.block_types import install_types

        install_types((self.config.get("generate", {}) or {}).get("block_types"))
        self.runtime = RuntimeDB(self.layout.runtime_db)
        self.registry = ModelRegistry(self.layout)
        self.client = LLMClient(self.registry, self.runtime, self.config)
        self.token = (self.layout.data_dir / ".local_token")
        from .security import load_or_create_token

        self.token_value = load_or_create_token(self.token)
        from .tasks import TaskManager

        self.tasks = TaskManager(self.runtime)
        # 注意：interrupt_stale() 只能在「服务器真正启动」时调用（见 main.py startup 钩子）。
        # 曾放在 __init__ 里导致任何 AppState 构造（含 pytest import src.api.main 时
        # 模块级 create_app()）都会把真实 runtime.db 里正在跑的任务打成 interrupted——
        # 实测一次测试运行直接掐死了用户的生成任务。

    def reload_config(self) -> dict:
        self.config = load_config(self.root)
        from src.core.block_types import install_types

        install_types((self.config.get("generate", {}) or {}).get("block_types"))
        # 热应用而非只换引用：thinking/推理深度/超时/并发上限都是客户端构造时固化的
        # 字段，只换 config 引用不会生效（设置改了没反应，要重启才生效）
        self.client.apply_config(self.config)
        return self.config

    def invalidate_client(self) -> None:
        """供应商/路由变更后重建 LLM 客户端。

        OpenAICompatAdapter 在创建时就把 base_url 与 API Key 固化进实例，
        LLMClient 又按 provider_id 缓存适配器——界面里改完 key 若不重建，
        运行中的服务会继续用旧 key 发请求（用户实测「换了 key 仍然无效」，
        只能重启服务才生效）。这里整体重建，改完立即生效。
        """
        self.client = LLMClient(self.registry, self.runtime, self.config)

    def save_model_routes(self, routes: dict) -> dict:
        cfg = dict(self.config)
        gen = dict(cfg.get("generate", {}))
        models = dict(gen.get("models", {}))
        # 槽位必须与后端真实消费的一致：default / outline / fill / agent / link / quiz / ocr
        # （此前只认前 5 个，向导里配 ocr/quiz 会被静默丢掉）
        for slot in ("default", "outline", "fill", "agent", "link", "quiz", "ocr"):
            if slot in routes:
                models[slot] = routes[slot]
        gen["models"] = models
        cfg["generate"] = gen
        save_config(self.root, cfg)
        return self.reload_config()

    def routed_models(self) -> list[str]:
        return [v for v in (self.config.get("generate", {}).get("models", {}) or {}).values() if v]
