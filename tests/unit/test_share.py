"""分享载荷回归：与文件导出同一套 Markdown，且块/图片/页码齐全可内嵌。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.api.main import create_app
from src.core.store import DocStore


def _seed_block(store: DocStore, ch: str) -> dict:
    anchor = store.anchor_rows(ch)[0]
    quote = (anchor.get("content") or "").strip()[:40]
    return store.create_block({"type": "motivation", "title": "分享测试块",
                               "content": "这是要被分享的正文，含公式 $a^2+b^2=c^2$。",
                               "sources": [{"anchor_id": anchor["anchor_id"],
                                            "quote": quote, "relation": "states"}],
                               "basis": "anchored", "order": 1.0},
                              author="human", chapter_id=ch)


def test_share_chapter_payload(sample_doc):
    layout, doc_id = sample_doc
    store = DocStore(layout, doc_id)
    ch = store.chapter_ids()[0]
    _seed_block(store, ch)
    app = create_app(layout.root)
    with TestClient(app, base_url="http://127.0.0.1:8787") as client:
        h = {"X-Local-Token": app.state.app_state.token_value}
        r = client.get(f"/api/v1/docs/{doc_id}/share?chapter_id={ch}", headers=h)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["filename"] == f"{ch}.md"
        assert "分享测试块" in data["markdown"], data["markdown"][:200]
        assert "$a^2+b^2=c^2$" in data["markdown"]
        blocks = data["blocks"]
        assert blocks and blocks[0]["content"].startswith("这是要被分享的正文")
        assert blocks[0]["sources"] and isinstance(blocks[0]["sources"][0]["page"], int)
        assert isinstance(data["figures"], dict)


def test_share_book_and_errors(sample_doc):
    layout, doc_id = sample_doc
    app = create_app(layout.root)
    with TestClient(app, base_url="http://127.0.0.1:8787") as client:
        h = {"X-Local-Token": app.state.app_state.token_value}
        r = client.get(f"/api/v1/docs/{doc_id}/share", headers=h)
        assert r.status_code == 200
        assert r.json()["filename"] == "_book.md"
        r = client.get(f"/api/v1/docs/{doc_id}/share?chapter_id=chZZ", headers=h)
        assert r.status_code == 404
        r = client.get("/api/v1/docs/doc-000000000000/share", headers=h)
        assert r.status_code == 404
