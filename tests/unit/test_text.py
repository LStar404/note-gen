"""normalize_text / text_hash / estimate_tokens / ID 生成（5.2、4.2.1）。"""

from __future__ import annotations

import pytest

from src.core.text import (base_anchor_id, doc_namespace, estimate_tokens, new_block_id,
                           normalize_text, search_normalize, slugify, text_hash, with_suffix)


def test_normalize_text_keeps_case_math_and_hyphen():
    assert normalize_text("  A  a-b  $$x=Y$$  ") == "A a-b $$x=Y$$"
    assert normalize_text("A") != normalize_text("a")
    assert normalize_text("x-y") != normalize_text("xy")


def test_normalize_text_nfc_and_whitespace():
    assert normalize_text("e\u0301") == "\u00e9"
    assert normalize_text("a\n\t b") == "a b"


def test_text_hash_stable_and_full_length():
    h = text_hash("行列式的定义")
    assert len(h) == 64 and all(c in "0123456789abcdef" for c in h)
    assert h == text_hash("  行列式的定义  ")
    assert h != text_hash("行列式的定义。")


def test_search_normalize_is_lossy_but_not_for_identity():
    assert search_normalize("A b") == "ab"
    assert text_hash("A b") == text_hash("A b")


def test_anchor_id_derivation():
    ns = doc_namespace("demo-la")
    assert len(ns) == 8
    base = base_anchor_id("demo-la", "内容")
    assert base.startswith(f"a_{ns}_")
    assert with_suffix(base, 1) == base
    assert with_suffix(base, 2) == base + "-2"


def test_new_block_id_format_and_uniqueness():
    existing = set()
    for _ in range(50):
        bid = new_block_id(existing)
        assert bid.startswith("b_") and len(bid) == 14
        existing.add(bid)
    assert len(existing) == 50


def test_estimate_tokens_cjk_vs_ascii():
    assert estimate_tokens("中文中文") == 4
    assert estimate_tokens("abcdefgh") == 2
    assert estimate_tokens("") == 0


def test_slugify():
    assert slugify("Linear Algebra 5E") == "linear-algebra-5e"
    assert slugify("线性代数") == "doc"
