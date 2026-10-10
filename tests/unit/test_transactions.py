"""4.2.2 可恢复提交协议（E-21）：提交、冲突、幂等前滚、崩溃恢复。"""

from __future__ import annotations

import json

import pytest

from src.core.errors import RevisionConflict
from src.core.store import DocMutation, DocStore
from src.core.transactions import doc_lock, recover_doc


def test_commit_increments_revision(store: DocStore):
    before = store.data_revision()
    block = store.create_block({"type": "motivation", "title": "动机", "content": "为什么",
                                "sources": [], "basis": "synthesis", "order": 1.0},
                               author="human", chapter_id="ch01")
    assert store.data_revision() == before + 1
    assert block["revision"] == 1 and block["generation_protected"] is True


def test_stale_base_revision_conflict(store: DocStore):
    base = store.data_revision()
    store.create_block({"type": "motivation", "title": "a", "content": "x", "sources": [],
                        "basis": "synthesis", "order": 1.0}, author="human", chapter_id="ch01")
    with pytest.raises(RevisionConflict):
        store.commit(DocMutation(), base_revision=base)


def test_history_rev_id_dedup(store: DocStore):
    block = store.create_block({"type": "motivation", "title": "a", "content": "x", "sources": [],
                                "basis": "synthesis", "order": 1.0}, author="human", chapter_id="ch01")
    rows1 = store.history_rows(block["id"])
    assert len(rows1) == 1
    # 同一 rev_id 重复提交不追加
    store.commit(DocMutation(history=[dict(rows1[0])]), sync_backlinks=False)
    assert len(store.history_rows(block["id"])) == 1


def test_recover_discards_uncommitted(store: DocStore, workspace):
    tx_dir = workspace.transactions_dir("sample-la") / "tx-uncommitted"
    tx_dir.mkdir(parents=True, exist_ok=True)
    (tx_dir / "intent.json").write_text(json.dumps({"tx_id": "tx-uncommitted", "targets": []}),
                                        encoding="utf-8")
    with doc_lock(workspace, "sample-la"):
        status = recover_doc(workspace, "sample-la")
    assert "tx-uncommitted" in status["discarded"]
    assert not tx_dir.exists()


def test_recover_redoes_committed(store: DocStore, workspace):
    """有 COMMIT 无 DONE：恢复必须幂等前滚。"""
    doc_dir = workspace.doc_dir("sample-la")
    target = doc_dir / "blocks" / "ch01.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_bytes(b"")
    original = target.read_bytes()
    tx_dir = doc_dir / "transactions" / "tx-redo"
    tx_dir.mkdir(parents=True, exist_ok=True)
    payload = b'{"id":"b_AAAAAAAAAAAA"}\n'
    (tx_dir / "payload_0.bin").write_bytes(payload)
    from src.core.jsonl import sha256_bytes, sha256_file

    intent = {"tx_id": "tx-redo", "doc_id": "sample-la", "base_revision": store.data_revision(),
              "new_revision": store.data_revision() + 1, "targets": [
                  {"rel_path": "blocks/ch01.jsonl", "op": "append",
                   "old_sha256": sha256_file(target),
                   "new_sha256": sha256_bytes(original + payload),
                   "payload": "payload_0.bin", "rev_id": None}]}
    (tx_dir / "intent.json").write_text(json.dumps(intent), encoding="utf-8")
    (tx_dir / "COMMIT").write_text("{}", encoding="utf-8")
    with doc_lock(workspace, "sample-la"):
        status = recover_doc(workspace, "sample-la")
    assert "tx-redo" in status["recovered"]
    assert target.read_bytes() == original + payload


def test_backlink_invariant_I3(store: DocStore):
    anchor_id = store.anchor_rows("ch01")[1]["anchor_id"]
    block = store.create_block(
        {"type": "definition", "title": "定义", "content": "内容",
         "sources": [{"anchor_id": anchor_id, "quote": store.anchors_by_id()[anchor_id]["content"][:10],
                      "relation": "defines"}], "order": 5.0},
        author="human", chapter_id="ch01")
    anchors = store.anchors_by_id()
    assert block["id"] in anchors[anchor_id]["note_block_ids"]
    store.soft_delete_block(block["id"])
    anchors = store.anchors_by_id()
    assert block["id"] not in anchors[anchor_id]["note_block_ids"]
