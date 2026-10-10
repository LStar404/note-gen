"""语义锚层（5.2 第三层，v2 启用）：段落级向量，维度配置化（默认 1024）。

provider：
- none        ：禁用（MVP 默认，embedding 保持 null）
- onnx_bge    ：bge 系段落级 ONNX 模型（model.onnx + tokenizer.json），均值池化 + L2 归一化
- hashed_lexical：**词法回退**（feature-hashing 字符 n-gram），仅用于打通向量检索链路，
                 不是语义向量；生产语义检索应配置 onnx_bge（配置项 embedding.provider）
"""

from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path

DEFAULT_DIM = 1024
_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)


class Embedder:
    def __init__(self, provider: str = "none", model_dir: str | None = None,
                 dim: int = DEFAULT_DIM, **_: object):
        self.provider = (provider or "none").lower()
        self.model_dir = Path(model_dir) if model_dir else None
        self.dim = int(dim)
        self._session = None
        self._tokenizer = None

    @property
    def enabled(self) -> bool:
        return self.provider in ("onnx_bge", "hashed_lexical")

    # ---- providers ----
    def _load_onnx(self):
        if self._session is not None:
            return
        if self.model_dir is None or not (self.model_dir / "model.onnx").exists():
            raise FileNotFoundError("embedding.provider=onnx_bge 需要 model_dir/model.onnx")
        import onnxruntime as ort  # 需要 [embed] 额外依赖

        from tokenizers import Tokenizer  # noqa: PLC0415

        self._session = ort.InferenceSession(str(self.model_dir / "model.onnx"))
        self._tokenizer = Tokenizer.from_file(str(self.model_dir / "tokenizer.json"))

    def _encode_onnx(self, text: str) -> list[float]:
        self._load_onnx()
        enc = self._tokenizer.encode(text)
        import numpy as np

        input_ids = np.array([enc.ids], dtype=np.int64)
        attention = np.array([enc.attention_mask], dtype=np.int64)
        outputs = self._session.run(None, {"input_ids": input_ids, "attention_mask": attention})
        hidden = outputs[0][0]                      # (seq, dim)
        mask = attention[0].reshape(-1, 1)
        pooled = (hidden * mask).sum(axis=0) / max(1, int(mask.sum()))
        norm = float(np.linalg.norm(pooled)) or 1.0
        return (pooled / norm).astype(float).tolist()

    def _encode_lexical(self, text: str) -> list[float]:
        """确定性 feature-hashing：字符 2/3-gram + 词形，仅作链路回退（见模块注释）。"""
        vec = [0.0] * self.dim
        tokens = _TOKEN_RE.findall(text.lower())
        features = list(tokens)
        compact = re.sub(r"\s+", "", text.lower())
        for n in (2, 3):
            features.extend(compact[i:i + n] for i in range(max(0, len(compact) - n + 1)))
        for feat in features:
            digest = hashlib.md5(feat.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:4], "little") % self.dim
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vec[idx] += sign
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def encode(self, texts: list[str]) -> list[list[float] | None]:
        if not self.enabled:
            return [None for _ in texts]
        out: list[list[float] | None] = []
        for text in texts:
            try:
                if self.provider == "onnx_bge":
                    out.append(self._encode_onnx(text))
                else:
                    out.append(self._encode_lexical(text))
            except Exception:
                out.append(None)   # 编码失败不阻塞管线，记录在报告
        return out


def embedder_from_config(config: dict) -> Embedder:
    section = (config or {}).get("embedding", {}) or {}
    return Embedder(provider=section.get("provider", "none"),
                    model_dir=section.get("model_dir"),
                    dim=int(section.get("dim", DEFAULT_DIM)))
