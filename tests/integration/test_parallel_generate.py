"""并行分章生成回归：两章同时跑 outline/fill，写入段按文档串行，不得互撞 RevisionConflict。"""

from __future__ import annotations

import threading
import time

from src.core.store import DocStore
from src.generate.engine import generate_chapter
from tests.integration.test_pipeline import CFG, make_client


def _payloads(store: DocStore, chapter_id: str) -> list:
    anchors = store.anchor_rows(chapter_id)
    a = anchors[1] if len(anchors) > 1 else anchors[0]
    aid, quote = a["anchor_id"], a["content"][:20]
    outline = {"blocks": [
        {"temp_id": "t1", "type": "definition", "title": f"{chapter_id} 的定义",
         "basis": "anchored",
         "sources": [{"anchor_id": aid, "quote": quote, "relation": "defines"}],
         "depends_on": [], "order": 1}]}
    fill = {"content": f"{chapter_id}：这是生成的内容。", "content_format": "md_latex",
            "tags": [],
            "sources_patch": [{"anchor_id": aid, "quote": quote, "relation": "defines"}],
            "basis": "anchored"}
    return [outline, fill]


def test_parallel_chapters_do_not_conflict_at_commit(sample_doc, monkeypatch):
    layout, doc_id = sample_doc
    store = DocStore(layout, doc_id)
    chapters = store.chapter_ids()
    assert len(chapters) >= 2
    first, second = chapters[0], chapters[1]
    # 客户端串行构造（models.json 写入非线程安全），生成再并行
    client_a, registry_a = make_client(layout, _payloads(store, first))
    client_b, registry_b = make_client(layout, _payloads(store, second))

    # 加宽「读基线→commit」的竞态窗口：没有写入串行化时两章必有一章撞 RevisionConflict
    import src.generate.engine as engine

    real_merge = engine.merge_candidates

    def slow_merge(*args, **kwargs):
        out = real_merge(*args, **kwargs)
        time.sleep(0.5)
        return out

    monkeypatch.setattr(engine, "merge_candidates", slow_merge)

    errors: list = []
    reports: dict[str, dict] = {}
    barrier = threading.Barrier(2)

    def run(ch, client, registry) -> None:
        try:
            barrier.wait(10)
            reports[ch] = generate_chapter(doc_id, ch, layout=layout, client=client,
                                           config=CFG, registry=registry,
                                           force=True, use_cache=False)
        except Exception as exc:  # noqa: BLE001 —— 线程里的失败带回断言
            errors.append((ch, repr(exc)))

    threads = [threading.Thread(target=run, args=(first, client_a, registry_a)),
               threading.Thread(target=run, args=(second, client_b, registry_b))]
    for th in threads:
        th.start()
    for th in threads:
        th.join(60)
    assert not errors, f"并行分章不得有提交冲突/异常: {errors}"
    assert reports[first]["status"] == "complete", reports.get(first)
    assert reports[second]["status"] == "complete", reports.get(second)
    assert store.block_rows(first), f"{first} 应有块"
    assert store.block_rows(second), f"{second} 应有块"


def test_parallel_chapters_preserve_all_blocks(sample_doc, monkeypatch):
    """并发分章多块：两章各 5 块并行生成，10 块一块不丢（全量重写丢更新检测）。"""
    import src.generate.engine as engine

    layout, doc_id = sample_doc
    store = DocStore(layout, doc_id)
    chapters = store.chapter_ids()
    first, second = chapters[0], chapters[1]

    def many(chapter_id: str) -> list:
        anchors = store.anchor_rows(chapter_id)
        a = anchors[1] if len(anchors) > 1 else anchors[0]
        aid, quote = a["anchor_id"], a["content"][:20]
        outline = {"blocks": [
            {"temp_id": f"t{i}", "type": "definition", "title": f"{chapter_id} 块{i}",
             "basis": "anchored",
             "sources": [{"anchor_id": aid, "quote": quote, "relation": "defines"}],
             "depends_on": [], "order": float(i)} for i in range(1, 6)]}
        fills = [{"content": f"{chapter_id} 第{i}块内容。", "content_format": "md_latex",
                  "tags": [],
                  "sources_patch": [{"anchor_id": aid, "quote": quote, "relation": "defines"}],
                  "basis": "anchored"} for i in range(1, 6)]
        return [outline, *fills]

    client_a, registry_a = make_client(layout, many(first))
    client_b, registry_b = make_client(layout, many(second))

    real_merge = engine.merge_candidates

    def slow_merge(*args, **kwargs):
        out = real_merge(*args, **kwargs)
        time.sleep(0.3)
        return out

    monkeypatch.setattr(engine, "merge_candidates", slow_merge)
    errors: list = []

    def run(ch, client, registry) -> None:
        try:
            generate_chapter(doc_id, ch, layout=layout, client=client,
                             config=CFG, registry=registry, force=True, use_cache=False)
        except Exception as exc:  # noqa: BLE001
            errors.append((ch, repr(exc)))

    threads = [threading.Thread(target=run, args=(first, client_a, registry_a)),
               threading.Thread(target=run, args=(second, client_b, registry_b))]
    for th in threads:
        th.start()
    for th in threads:
        th.join(60)
    assert not errors, errors
    n1 = len(store.block_rows(first))
    n2 = len(store.block_rows(second))
    assert n1 == 5, f"{first} 应有 5 块，实得 {n1}（并行丢更新）"
    assert n2 == 5, f"{second} 应有 5 块，实得 {n2}（并行丢更新）"
