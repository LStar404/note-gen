"""临时冒烟脚本：ingest → anchor → generate(Dummy) → link → export → reindex。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.llm import DummyProvider, LLMClient
from src.core.model_registry import ModelRegistry
from src.core.paths import Layout
from src.core.store import DocStore
from src.anchor.chunker import chunk_and_anchor
from src.ingest.parse_pdf import parse_pdf
from src.generate.engine import generate_chapter
from src.link.backlink import link_doc
from src.export.md_export import export_md_chapter, export_md_book
from src.db.reindex import reindex_doc

ROOT = Path(__file__).resolve().parents[1]
layout = Layout(ROOT)
layout.ensure_base()

pdf = ROOT / "tests" / "golden" / "sample.pdf"
manifest = parse_pdf(pdf, layout.ingest_root, "pypdf", layout=layout, doc_id="sample-la",
                     title="Sample Linear Algebra", profile="textbook")
print("chapters:", json.dumps(manifest["chapters"], ensure_ascii=False))
print("pages:", [(p["page"], p["parse_status"]) for p in manifest["pages"]])

for ch in manifest["chapters"]:
    n = chunk_and_anchor("sample-la", ch["chapter_id"], layout=layout)
    print("anchors", ch["chapter_id"], n)

store = DocStore(layout, "sample-la")
rows = store.anchor_rows("ch01")
print("first anchors:", [(r["anchor_id"][:16], r["content_type"], r["content"][:40]) for r in rows[:5]])

outline_payload = {"blocks": [
    {"temp_id": "t1", "type": "definition", "title": "行列式的定义", "basis": "anchored",
     "sources": [{"anchor_id": rows[1]["anchor_id"], "quote": rows[1]["content"][:20], "relation": "defines"}],
     "depends_on": [], "order": 1},
    {"temp_id": "t2", "type": "motivation", "title": "为什么要引入行列式", "basis": "synthesis",
     "sources": [], "depends_on": ["t1"], "order": 2},
]}
fill_payload = {"content": "设 $A=(a_{ij})$，则 $\\det A$ 表示……\n\n**要点**：行列式非零当且仅当可逆。",
                "content_format": "md_latex", "tags": ["行列式"], "sources_patch": [
                    {"anchor_id": rows[1]["anchor_id"], "quote": rows[1]["content"][:20], "relation": "defines"}],
                "basis": "anchored"}
fill_motivation = {"content": "为判断线性方程组解的唯一性引入行列式。", "content_format": "md_latex",
                   "tags": [], "sources_patch": [], "basis": "synthesis"}

dummy = DummyProvider([outline_payload, fill_payload, fill_motivation])
registry = ModelRegistry(layout)
cfg = {"generate": {"models": {"default": "dummy/fake", "outline": "dummy/fake", "fill": "dummy/fake"},
                    "concurrency": 1, "temperature": 0.2, "max_cost_per_chapter": 0.0},
       "cache": {"dir": "cache/gen"}, "match": {}, "history": {}}
client = LLMClient(registry, __import__("src.db.runtime", fromlist=["RuntimeDB"]).RuntimeDB(layout.runtime_db),
                   cfg, overrides={"dummy": dummy})
registry.upsert_provider({"id": "dummy", "label": "Dummy", "protocol": "openai_compat",
                          "base_url": "http://127.0.0.1:1", "api_key": None,
                          "capabilities": {"json_schema": True, "tools": True, "vision": False},
                          "models": [{"id": "fake", "context_window": 8192, "max_output_tokens": 2048,
                                      "capabilities": {"json_schema": True, "tools": True, "vision": False},
                                      "pricing": {"input_per_million": 0, "output_per_million": 0, "currency": "USD"}}]})

report = generate_chapter("sample-la", "ch01", layout=layout, client=client, config=cfg,
                          registry=registry, use_cache=False, force=True)
print("generate report:", json.dumps({k: v for k, v in report.items() if k in
      ("status", "created", "updated", "skipped_protected", "failures", "cache_hit")}, ensure_ascii=False))

print("link:", json.dumps(link_doc("sample-la", layout=layout), ensure_ascii=False)[:300])
out_dir = ROOT / "notes_out" / "sample-la"
p1 = export_md_chapter(store, "ch01", out_dir=out_dir)
p2 = export_md_book(store, out_dir=out_dir)
print("exported:", p1, p2)
print(reindex_doc(layout, "sample-la"))
print("--- md head ---")
print(p1.read_text(encoding="utf-8")[:800])
