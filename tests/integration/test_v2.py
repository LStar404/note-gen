"""v2 增量验收：eval 自动化基线回归、Anki 闪卡导出、sqlite-vec 语义检索。"""

from __future__ import annotations

import json
from pathlib import Path

from src.core.store import DocStore
from src.eval.runner import run_eval
from src.export.anki_export import collect_cards, export_anki
from tests.integration.test_pipeline import CFG, make_client
from src.generate.engine import generate_chapter

ROOT = Path(__file__).resolve().parents[2]
CASES = ROOT / "tests" / "eval" / "cases.jsonl"
LABELS = ROOT / "tests" / "eval" / "labels.json"


def _generate_full(store: DocStore, layout, chapter_ids=("ch01", "ch02")):
    """按评测集期望生成块（DummyProvider 固定输出）。"""
    anchors = {a["anchor_id"]: a for a in store.all_anchor_rows()}

    def q(aid, n=30):
        return anchors[aid]["content"][:n]

    outline = {"blocks": [
        {"temp_id": "t1", "type": "definition", "title": "行列式的定义", "basis": "anchored",
         "sources": [{"anchor_id": "a_51443b62_be30e488eee16394",
                      "quote": q("a_51443b62_be30e488eee16394"), "relation": "defines"}],
         "depends_on": [], "order": 1},
        {"temp_id": "t2", "type": "definition", "title": "行列式的计算公式", "basis": "anchored",
         "sources": [{"anchor_id": "a_51443b62_906de152bf965735",
                      "quote": q("a_51443b62_906de152bf965735"), "relation": "defines"}],
         "depends_on": [], "order": 2},
        {"temp_id": "t3", "type": "theorem", "title": "可逆判定定理", "basis": "anchored",
         "sources": [{"anchor_id": "a_51443b62_943936f7bf3d53ee",
                      "quote": q("a_51443b62_943936f7bf3d53ee"), "relation": "states"}],
         "depends_on": [], "order": 3},
        {"temp_id": "t4", "type": "motivation", "title": "为什么引入行列式", "basis": "synthesis",
         "sources": [], "depends_on": ["t1"], "order": 0},
    ]}
    outline2 = {"blocks": [
        {"temp_id": "t1", "type": "definition", "title": "单位矩阵", "basis": "anchored",
         "sources": [{"anchor_id": "a_51443b62_09770b6491e1136b",
                      "quote": q("a_51443b62_09770b6491e1136b"), "relation": "defines"}],
         "depends_on": [], "order": 1},
    ]}

    fills = [
        {"content": "行列式的定义：the determinant of an n by n matrix A 由元素完全确定。",
         "content_format": "md_latex", "tags": ["定义"],
         "sources_patch": [{"anchor_id": "a_51443b62_be30e488eee16394",
                            "quote": q("a_51443b62_be30e488eee16394"), "relation": "defines"}],
         "basis": "anchored"},
        {"content": "由 Leibniz 公式：$$\\det A = \\sum_{\\sigma} \\operatorname{sign}(\\sigma) \\prod_{i} a_{i,\\sigma(i)}$$",
         "content_format": "md_latex", "tags": ["公式"],
         "sources_patch": [{"anchor_id": "a_51443b62_906de152bf965735",
                            "quote": q("a_51443b62_906de152bf965735"), "relation": "defines"}],
         "basis": "anchored"},
        {"content": "定理：a square matrix is invertible if and only if its determinant is non zero。",
         "content_format": "md_latex", "tags": ["定理"],
         "sources_patch": [{"anchor_id": "a_51443b62_943936f7bf3d53ee",
                            "quote": q("a_51443b62_943936f7bf3d53ee"), "relation": "states"}],
         "basis": "anchored"},
        {"content": "为判断线性方程组何时有唯一解（unique solution）而引入行列式。",
         "content_format": "md_latex", "tags": ["动机"], "sources_patch": [], "basis": "synthesis"},
    ]
    fills2 = [
        {"content": "定义：the identity matrix has ones on the diagonal and zeros elsewhere。",
         "content_format": "md_latex", "tags": ["定义"],
         "sources_patch": [{"anchor_id": "a_51443b62_09770b6491e1136b",
                            "quote": q("a_51443b62_09770b6491e1136b"), "relation": "defines"}],
         "basis": "anchored"},
    ]
    client, registry = make_client(layout, [outline, *fills])
    generate_chapter("sample-la", "ch01", layout=layout, client=client, config=CFG,
                     registry=registry, use_cache=False, force=True)
    client2, registry2 = make_client(layout, [outline2, *fills2])
    generate_chapter("sample-la", "ch02", layout=layout, client=client2, config=CFG,
                     registry=registry2, use_cache=False, force=True)


def test_eval_dimensions_and_regression(store: DocStore, sample_doc, tmp_path):
    layout, doc_id = sample_doc
    _generate_full(store, layout)

    baseline = tmp_path / "eval_baseline.json"
    first = run_eval(layout, doc_id, cases_path=CASES, labels_path=LABELS,
                     baseline_path=baseline, update_baseline=True)
    assert first["cases"] == 5
    assert first["dimensions"]["coverage"] == 1.0
    assert first["dimensions"]["faithfulness"] == 1.0
    assert first["dimensions"]["formula"] == 1.0
    assert first["human_labelled"] == 2

    # 基线对比：正常运行无回归
    ok = run_eval(layout, doc_id, cases_path=CASES, labels_path=LABELS, baseline_path=baseline)
    assert ok.get("regressions") in ([], None)

    # 制造回归：删除定理块 → faithfulness/coverage 应跌破基线
    for block in store.block_rows("ch01"):
        if block["type"] == "theorem":
            store.soft_delete_block(block["id"], author="human")
    bad = run_eval(layout, doc_id, cases_path=CASES, labels_path=LABELS, baseline_path=baseline)
    dims = {r["dimension"] for r in bad.get("regressions", [])}
    assert dims & {"faithfulness", "coverage", "formula"}


def test_anki_export(store: DocStore, sample_doc, tmp_path):
    layout, doc_id = sample_doc
    _generate_full(store, layout)
    cards = collect_cards(store)
    assert {c["type"] for c in cards} <= {"definition", "theorem", "motivation"}
    assert any(c["type"] == "theorem" for c in cards)
    assert any("p." in "、".join(f"p.{p}" for p in c["pages"]) for c in cards if c["pages"])

    out = export_anki(store, None, out_dir=tmp_path)
    assert out.exists() and out.stat().st_size > 0
    if out.suffix == ".apkg":
        import zipfile

        with zipfile.ZipFile(out) as zf:
            names = zf.namelist()
            assert "collection.anki2" in names


def test_vector_search(store: DocStore, sample_doc):
    layout, doc_id = sample_doc
    from src.anchor.embed import Embedder
    from src.db.reindex import reindex_doc
    from src.db.vec import search_vec, vec_available

    assert vec_available(), "sqlite-vec 应已安装"
    embedder = Embedder(provider="hashed_lexical", dim=64)
    rows = store.anchor_rows("ch01")
    vecs = embedder.encode([r["content"] for r in rows])
    updates = {"ch01": [dict(r, embedding=v) for r, v in zip(rows, vecs)]}
    from src.core.store import DocMutation

    store.commit(DocMutation(anchors=updates), sync_backlinks=True)
    meta = reindex_doc(layout, doc_id, vec_dim=64)
    assert meta["vectors"] == len(rows)

    query = embedder.encode(["the determinant of a matrix is a scalar value"])[0]
    hits = search_vec(layout, doc_id, query, k=3, threshold=0.1)
    assert hits and hits[0]["anchor_id"] == rows[1]["anchor_id"]
