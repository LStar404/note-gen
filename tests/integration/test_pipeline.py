"""黄金样本端到端（E-02/E-03/E-07/E-11/E-14）：ingest→anchor→generate→link→export→reindex。"""

from __future__ import annotations


from src.core.llm import DummyProvider, LLMClient
from src.core.model_registry import ModelRegistry
from src.core.paths import Layout
from src.core.store import DocStore
from src.db.queries import blocks_for_page, match_selection
from src.db.reindex import reindex_doc
from src.db.runtime import RuntimeDB
from src.export.md_export import export_md_book, export_md_chapter
from src.generate.engine import generate_chapter
from src.link.backlink import link_doc

CFG = {"generate": {"models": {"default": "dummy/fake", "outline": "dummy/fake", "fill": "dummy/fake"},
                    "concurrency": 1, "temperature": 0.2, "max_cost_per_chapter": 0.0},
       "cache": {"dir": "cache/gen"}, "match": {"contain_ratio": 0.8, "cover_ratio": 0.5, "top_k": 8},
       "history": {"keep_recent": 50}}


def make_client(layout: Layout, responses: list):
    registry = ModelRegistry(layout)
    registry.upsert_provider({"id": "dummy", "label": "Dummy", "protocol": "openai_compat",
                              "base_url": "http://127.0.0.1:1", "api_key": None,
                              "capabilities": {"json_schema": True, "tools": True, "vision": False},
                              "models": [{"id": "fake", "context_window": 8192,
                                          "max_output_tokens": 2048,
                                          "capabilities": {"json_schema": True, "tools": True,
                                                           "vision": False},
                                          "pricing": {"input_per_million": 0,
                                                      "output_per_million": 0,
                                                      "currency": "USD"}}]})
    client = LLMClient(registry, RuntimeDB(layout.runtime_db), CFG, overrides={"dummy": DummyProvider(responses)})
    return client, registry


def test_full_pipeline(store: DocStore, sample_doc):
    layout, doc_id = sample_doc
    anchors = store.anchor_rows("ch01")
    anchor = anchors[1]["anchor_id"]
    quote = anchors[1]["content"][:20]

    outline = {"blocks": [
        {"temp_id": "t1", "type": "definition", "title": "行列式的定义", "basis": "anchored",
         "sources": [{"anchor_id": anchor, "quote": quote, "relation": "defines"}],
         "depends_on": [], "order": 1},
        {"temp_id": "t2", "type": "motivation", "title": "引入动机", "basis": "synthesis",
         "sources": [], "depends_on": ["t1"], "order": 2},
    ]}
    fill1 = {"content": "设 $A=(a_{ij})$，则 $\\det A$ 表示体积因子。", "content_format": "md_latex",
             "tags": ["行列式"],
             "sources_patch": [{"anchor_id": anchor, "quote": quote, "relation": "defines"}],
             "basis": "anchored"}
    fill2 = {"content": "为判断解的唯一性而引入。", "content_format": "md_latex", "tags": [],
             "sources_patch": [], "basis": "synthesis"}
    client, registry = make_client(layout, [outline, fill1, fill2])

    report = generate_chapter(doc_id, "ch01", layout=layout, client=client, config=CFG,
                              registry=registry, force=True, use_cache=False)
    assert report["status"] == "complete"
    assert len(report["created"]) == 2

    blocks = store.block_rows("ch01")
    assert {b["type"] for b in blocks} == {"definition", "motivation"}
    definition = next(b for b in blocks if b["type"] == "definition")
    assert definition["heading_anchor_id"] == anchors[0]["anchor_id"]   # 小节标题由 heading 锚点驱动

    # I3 双向镜像
    back = store.anchors_by_id()
    assert definition["id"] in back[anchor]["note_block_ids"]

    # 导出（E-07 md 部分）
    out_dir = layout.notes_doc_dir(doc_id)
    path = export_md_chapter(store, "ch01", out_dir=out_dir)
    text = path.read_text(encoding="utf-8")
    assert "note://anchor/" in text and "📖p." in text
    assert "（AI 归纳）" in text
    assert export_md_book(store, out_dir=out_dir).exists()

    # link + reindex（E-14）
    link_doc(doc_id, layout=layout)
    meta = reindex_doc(layout, doc_id)
    assert meta["blocks"] >= 2

    # 教材→笔记（6.3）
    page_blocks = blocks_for_page(layout, doc_id, 1)
    assert any(b["block_id"] == definition["id"] for b in page_blocks)


def test_selection_match(store: DocStore, sample_doc):
    layout, doc_id = sample_doc
    anchor = store.anchor_rows("ch01")[1]
    bbox = anchor["bbox"]
    hits = match_selection(layout, doc_id, 1, [bbox], anchor["content"][:30], CFG)
    assert hits and hits[0]["anchor_id"] == anchor["anchor_id"]
    assert hits[0]["scores"]["cover_anchor"] >= 0.8


def test_generate_is_idempotent_with_cache(store: DocStore, sample_doc):
    layout, doc_id = sample_doc
    anchors = store.anchor_rows("ch01")
    anchor, quote = anchors[1]["anchor_id"], anchors[1]["content"][:20]
    outline = {"blocks": [{"temp_id": "t1", "type": "definition", "title": "定义", "basis": "anchored",
                           "sources": [{"anchor_id": anchor, "quote": quote, "relation": "defines"}],
                           "depends_on": [], "order": 1}]}
    fill = {"content": "内容", "content_format": "md_latex", "tags": [],
            "sources_patch": [{"anchor_id": anchor, "quote": quote, "relation": "defines"}],
            "basis": "anchored"}
    client, registry = make_client(layout, [outline, fill])
    first = generate_chapter(doc_id, "ch01", layout=layout, client=client, config=CFG,
                             registry=registry, use_cache=True, force=True)
    assert len(first["created"]) == 1
    second = generate_chapter(doc_id, "ch01", layout=layout, client=client, config=CFG,
                              registry=registry, use_cache=True)
    assert second["cache_hit"] is True
    assert second["skipped_identical"]            # 内容相同跳过，不写历史
    block = store.block_rows("ch01")[0]
    assert len(store.history_rows(block["id"])) == 1


def test_manual_edit_survives_regeneration(store: DocStore, sample_doc):
    """E-13：人工编辑后再生成，人工块保留并报告 skipped_protected。"""
    layout, doc_id = sample_doc
    anchors = store.anchor_rows("ch01")
    anchor, quote = anchors[1]["anchor_id"], anchors[1]["content"][:20]
    outline = {"blocks": [{"temp_id": "t1", "type": "definition", "title": "定义", "basis": "anchored",
                           "sources": [{"anchor_id": anchor, "quote": quote, "relation": "defines"}],
                           "depends_on": [], "order": 1}]}
    fill = {"content": "AI 内容", "content_format": "md_latex", "tags": [],
            "sources_patch": [{"anchor_id": anchor, "quote": quote, "relation": "defines"}],
            "basis": "anchored"}
    client, registry = make_client(layout, [outline, fill])
    generate_chapter(doc_id, "ch01", layout=layout, client=client, config=CFG,
                     registry=registry, use_cache=False, force=True)
    block = store.block_rows("ch01")[0]
    store.update_block(block["id"], {"content": "人工修改的内容"}, expected_revision=block["revision"],
                       author="human", message="人工编辑")

    client2, registry2 = make_client(layout, [outline, dict(fill, content="AI 新内容")])
    report = generate_chapter(doc_id, "ch01", layout=layout, client=client2, config=CFG,
                              registry=registry2, use_cache=False, force=True)
    assert report["skipped_protected"] == [block["id"]]
    assert store.get_block(block["id"])["content"] == "人工修改的内容"
    assert store.get_block(block["id"])["generation_protected"] is True


def test_partial_failure_keeps_old_blocks(store: DocStore, sample_doc):
    layout, doc_id = sample_doc
    anchors = store.anchor_rows("ch01")
    anchor, quote = anchors[1]["anchor_id"], anchors[1]["content"][:20]
    outline = {"blocks": [
        {"temp_id": "t1", "type": "definition", "title": "定义", "basis": "anchored",
         "sources": [{"anchor_id": anchor, "quote": quote, "relation": "defines"}],
         "depends_on": [], "order": 1},
        {"temp_id": "t2", "type": "theorem", "title": "定理", "basis": "anchored",
         "sources": [{"anchor_id": anchor, "quote": quote, "relation": "states"}],
         "depends_on": [], "order": 2}]}
    fill_bad = {"content": "", "content_format": "md_latex", "tags": [], "sources_patch": [], "basis": "anchored"}
    client, registry = make_client(layout, [
        outline,
        {"content": "好内容", "content_format": "md_latex", "tags": [],
         "sources_patch": [{"anchor_id": anchor, "quote": quote, "relation": "defines"}], "basis": "anchored"},
        fill_bad, fill_bad, fill_bad])
    report = generate_chapter(doc_id, "ch01", layout=layout, client=client, config=CFG,
                              registry=registry, use_cache=False, force=True)
    assert report["status"] == "partial"
    assert report["failures"]
    assert store.block_rows("ch01")          # 其它合法候选仍可提交
