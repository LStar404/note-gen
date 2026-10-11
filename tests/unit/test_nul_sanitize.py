"""NUL 净化回归：笔记文本里的 NUL 必须还原为反斜杠（数学渲染炸裂的根因防护）。"""

from __future__ import annotations

from src.generate.validate_retry import validate_fill_payload

AID = "a_92e41455_dc6f1a2453d5ef97"
BS = chr(92)
NUL = chr(0)


def _anchors():
    return {AID: {"anchor_id": AID, "content": "原文片段在这里作为锚点正文",
                  "page": 1, "reading_order": 1}}


def _spec():
    return {"temp_id": "t1", "type": "definition", "title": "定义",
            "basis": "anchored",
            "sources": [{"anchor_id": AID, "quote": "原文片段", "relation": "defines"}],
            "depends_on": []}


def _payload(content: str, title: str = "定义") -> dict:
    return {"content": content, "title": title, "content_format": "md_latex",
            "tags": [], "basis": "anchored",
            "sources_patch": [{"anchor_id": AID, "quote": "原文片段",
                               "relation": "defines"}]}


def test_nul_restored_to_backslash_in_content():
    dirty = "$$" + NUL + "mathbb{Q} = K_0 " + NUL + "subset K_1$$"
    out = validate_fill_payload(_payload(dirty, title="标题" + NUL + "测试"),
                                allowed_anchor_ids={AID},
                                anchors_by_id=_anchors(), spec=_spec())
    assert NUL not in out["content"]
    assert out["content"] == "$$" + BS + "mathbb{Q} = K_0 " + BS + "subset K_1$$"
    assert NUL not in out["title"]
    assert out["title"] == "标题" + BS + "测试"


def test_clean_content_untouched():
    clean = "$$" + BS + "mathbb{Q}$$ 正文"
    out = validate_fill_payload(_payload(clean), allowed_anchor_ids={AID},
                                anchors_by_id=_anchors(), spec=_spec())
    assert out["content"] == clean
