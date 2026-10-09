"""语义锚层（v2 启用）：bge 系段落级向量，MVP 为 null。"""

from __future__ import annotations

DEFAULT_DIM = 1024


class Embedder:
    def __init__(self, model: str | None = None, dim: int = DEFAULT_DIM):
        self.model = model
        self.dim = dim

    @property
    def enabled(self) -> bool:
        return self.model is not None

    def encode(self, texts: list[str]) -> list[list[float] | None]:
        """MVP 不启用：返回 None 占位（4.4.2 embedding 可空）。"""
        return [None for _ in texts]
