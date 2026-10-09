"""文本归一化、哈希、token 估算与 ID 生成（规范 4.2.1 / 5.2）。"""

from __future__ import annotations

import hashlib
import re
import secrets
import string
import unicodedata
from datetime import datetime, timezone

_BASE62 = string.ascii_letters + string.digits


def utc_now_iso() -> str:
    """时间规范：UTC ISO-8601，格式 YYYY-MM-DDTHH:MM:SSZ。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_text(s: str) -> str:
    """身份与 quote 验证用归一化；保持大小写、数学符号、连字符和标点（5.2 精确算法）。"""
    s = unicodedata.normalize("NFC", s or "")
    return re.sub(r"\s+", " ", s).strip()


def text_hash(s: str) -> str:
    """归一化内容 SHA-256 hex（64 位，不带前缀）。"""
    return hashlib.sha256(normalize_text(s).encode("utf-8")).hexdigest()


def search_normalize(s: str) -> str:
    """仅供检索；不得用于身份哈希（5.2）。"""
    s = unicodedata.normalize("NFKC", s or "").casefold()
    return re.sub(r"\s+", "", s)


def estimate_tokens(s: str) -> int:
    """token 估算：ASCII 约 4 字符/token，CJK 约 1 字符/token；只作预估（5.3.2）。"""
    if not s:
        return 0
    cjk = sum(1 for ch in s if "\u4e00" <= ch <= "\u9fff" or "\u3000" <= ch <= "\u303f")
    other = len(s) - cjk
    return cjk + (other + 3) // 4


def doc_namespace(doc_id: str) -> str:
    return hashlib.sha256(doc_id.encode("utf-8")).hexdigest()[:8]


def base_anchor_id(doc_id: str, content: str) -> str:
    return f"a_{doc_namespace(doc_id)}_{text_hash(content)[:16]}"


def with_suffix(base_id: str, n: int) -> str:
    """重复实例后缀：第一个用基础 ID，后续 -2/-3/...（5.2）。"""
    return base_id if n <= 1 else f"{base_id}-{n}"


def new_block_id(existing: set[str] | None = None) -> str:
    existing = existing or set()
    for _ in range(64):
        cand = "b_" + "".join(secrets.choice(_BASE62) for _ in range(12))
        if cand not in existing:
            return cand
    raise RuntimeError("block_id 生成冲突，重试失败")


def new_tx_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f") + "-" + secrets.token_hex(4)


def slugify(value: str, fallback: str = "doc") -> str:
    """doc_id 候选：仅 [a-z0-9-]，中文/空名用 fallback（4.2.1）。"""
    v = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode("ascii")
    v = re.sub(r"[^a-zA-Z0-9]+", "-", v).strip("-").lower()
    v = re.sub(r"-{2,}", "-", v)
    return v if len(v) >= 4 else fallback
