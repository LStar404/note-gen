"""选区匹配 top_k 回归：配置必须生效（曾被双重切片写死为 10）。"""

from __future__ import annotations

from src.db import queries


def _anchors(n: int) -> list[dict]:
    return [{"anchor_id": f"a{i}", "content": f"文本{i}", "reading_order": i,
             "bbox_x0": 0.0, "bbox_y0": i * 20.0,
             "bbox_x1": 100.0, "bbox_y1": i * 20.0 + 15.0} for i in range(n)]


def test_match_selection_respects_top_k(monkeypatch):
    monkeypatch.setattr(queries, "anchors_on_page", lambda layout, doc_id, page: _anchors(12))
    sel = [0.0, 0.0, 100.0, 240.0]      # 覆盖全部 12 个锚点
    cfg8 = {"match": {"contain_ratio": 0.8, "cover_ratio": 0.5, "top_k": 8}}
    assert len(queries.match_selection(None, "d", 1, [sel], "", cfg8)) == 8
    cfg3 = {"match": {"contain_ratio": 0.8, "cover_ratio": 0.5, "top_k": 3}}
    assert len(queries.match_selection(None, "d", 1, [sel], "", cfg3)) == 3, "top_k 调小必须生效"
    cfg20 = {"match": {"contain_ratio": 0.8, "cover_ratio": 0.5, "top_k": 20}}
    assert len(queries.match_selection(None, "d", 1, [sel], "", cfg20)) == 12, "top_k 调大不得被 10 截断"
