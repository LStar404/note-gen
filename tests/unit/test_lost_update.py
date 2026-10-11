"""丢更新回归（I7/11.6.3）：读-改-写窗口内的并发改动不得被整文件替换静默冲掉。"""

from __future__ import annotations

import pytest

from src.core.errors import RevisionConflict
from src.core.store import DocMutation, DocStore


def _mk(store: DocStore, chapter: str, title: str) -> dict:
    return store.create_block({"type": "motivation", "title": title, "content": "x",
                               "sources": [], "basis": "synthesis", "order": 1.0},
                              author="human", chapter_id=chapter)


def test_concurrent_edit_is_not_silently_clobbered(sample_doc):
    """两个写者同时读、先后写：后写者必须 RevisionConflict，不得覆盖先写者的改动。"""
    layout, doc_id = sample_doc
    store = DocStore(layout, doc_id)
    ch = store.chapter_ids()[0]
    b1 = _mk(store, ch, "甲")
    b2 = _mk(store, ch, "乙")

    # 写者 A：读取全章行快照并记住基线（读-改-写窗口开始）
    writer_a = DocStore(layout, doc_id)
    base_a = writer_a.data_revision()
    rows_a = [dict(b) for b in writer_a.block_rows(ch, include_deleted=True)]
    rows_a = [{**b, "title": "甲·A改", "revision": int(b["revision"]) + 1}
              if b["id"] == b1["id"] else b for b in rows_a]

    # 写者 B：在同一窗口内抢先提交对 b2 的编辑（推高 data_revision）
    writer_b = DocStore(layout, doc_id)
    writer_b.update_block(b2["id"], {"title": "乙·B改"}, expected_revision=None, author="human")

    # A 迟到提交：必须冲突失败，而不是把 B 的改动整文件冲掉
    with pytest.raises(RevisionConflict):
        writer_a.commit(DocMutation(blocks={ch: rows_a}), base_revision=base_a)

    fresh = DocStore(layout, doc_id)
    assert fresh.get_block(b2["id"])["title"] == "乙·B改", "并发改动不得被静默覆盖"
    assert fresh.get_block(b1["id"])["title"] == "甲"


def test_mutators_capture_baseline_at_read_time(sample_doc, monkeypatch):
    """store 的 mutator（update_block 等）CAS 基线必须取自读取时刻：
    读-改-写窗口内插入一次他人提交后，本次提交必须 409 而非静默成功。"""
    layout, doc_id = sample_doc
    store = DocStore(layout, doc_id)
    ch = store.chapter_ids()[0]
    b1 = _mk(store, ch, "甲")
    b2 = _mk(store, ch, "乙")

    real_commit = DocStore.commit
    injected = {"done": False}

    def racing_commit(self, mutation, **kw):
        if not injected["done"]:
            injected["done"] = True
            # 在本次 mutator 提交前，另一写者抢先提交（推高 data_revision）
            DocStore(self.layout, self.doc_id).update_block(
                b2["id"], {"title": "乙·插队"}, expected_revision=None, author="human")
        return real_commit(self, mutation, **kw)

    monkeypatch.setattr(DocStore, "commit", racing_commit)
    with pytest.raises(RevisionConflict):
        store.update_block(b1["id"], {"title": "甲·改"}, expected_revision=None, author="human")

    monkeypatch.setattr(DocStore, "commit", real_commit)
    fresh = DocStore(layout, doc_id)
    assert fresh.get_block(b2["id"])["title"] == "乙·插队", "插队提交必须存活"
    assert fresh.get_block(b1["id"])["title"] == "甲", "冲突提交不得生效"
