"""图资源刷新回归：无图时优雅返回；id/图注规则稳定。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.api.main import create_app
from src.ingest.figures import _caption_for, _stable_fig_id, pick_ref_target


def test_pick_ref_target_rejects_near_tie():
    """近距两图（同破平级、间距差 <60pt）是歧义，不得猜——原判定要求间距完全相等，
    歧义几乎永不触发，近距两图会被错贴。"""
    assert pick_ref_target([(100.0, 0, "fig_a"), (120.0, 0, "fig_b")]) is None
    assert pick_ref_target([(100.0, 0, "fig_a"), (100.0, 0, "fig_b")]) is None


def test_pick_ref_target_clear_winner_and_tier():
    """差距明显取最近；破平级不同不算歧义（首选来源页破平）。"""
    assert pick_ref_target([(100.0, 0, "fig_a"), (200.0, 0, "fig_b")]) == "fig_a"
    assert pick_ref_target([(100.0, 1, "fig_x"), (110.0, 0, "fig_y")]) == "fig_x"
    assert pick_ref_target([]) is None


def test_stable_fig_id_deterministic():
    """figure id 必须跨次刷新稳定（块内容里存的是它，换号=又一批破图）。"""
    assert _stable_fig_id(15, 0) == "fig_00001500"
    assert _stable_fig_id(15, 0) == _stable_fig_id(15, 0)
    assert _stable_fig_id(15, 0) != _stable_fig_id(15, 1)


def test_caption_picks_line_below_image():
    img = [100, 300, 400, 500]
    blocks = [{"bbox": [100, 280, 400, 300], "text": "图 1.2 三角形示意图"},
              {"bbox": [100, 200, 400, 220], "text": "正文段落"}]
    assert _caption_for(blocks, img) == "图 1.2 三角形示意图"
    assert _caption_for([], img) == "图"


def test_refresh_figures_graceful_without_images(sample_doc):
    layout, doc_id = sample_doc
    app = create_app(layout.root)
    token = app.state.app_state.token_value
    with TestClient(app, base_url="http://127.0.0.1:8787") as c:
        c.headers.update({"X-Local-Token": token})
        r = c.post(f"/api/v1/docs/{doc_id}/refresh-figures", json={})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["figures"] >= 0
        assert body["patched_blocks"] == 0


def test_refresh_figures_patch_does_not_clobber_concurrent_edit(sample_doc, monkeypatch):
    """图片引用修复的读-改-写窗口内并发改动不得被整文件替换静默冲掉（丢更新）。
    原实现 CAS 基线取在提交时刻而非读取时刻，形同虚设。"""
    import json

    from src.core.store import DocStore

    layout, doc_id = sample_doc
    store = DocStore(layout, doc_id)
    ch = store.chapter_ids()[0]
    anchors = store.anchor_rows(ch)
    aid = anchors[0]["anchor_id"]
    quote = (anchors[0].get("content") or "")[:20]
    page = int(anchors[0]["page"])

    # 带悬空图引用的块（源锚点在 page）+ 同页恰一张图（唯一候选）
    store.create_block({"type": "motivation", "title": "图引修复", "content": "见 ![fig_00000999]",
                        "sources": [{"anchor_id": aid, "quote": quote, "relation": "motivates"}],
                        "basis": "synthesis", "order": 1.0}, author="human", chapter_id=ch)
    other = store.create_block({"type": "motivation", "title": "乙", "content": "x",
                                "sources": [], "basis": "synthesis", "order": 2.0},
                               author="human", chapter_id=ch)
    pf = layout.ingest_root / doc_id / "pages" / f"{page:04d}.json"
    data = json.loads(pf.read_text(encoding="utf-8"))
    data["blocks"].append({"type": "figure", "level": 0, "text": "图",
                           "bbox": [100.0, 100.0, 200.0, 200.0],
                           "figure_id": "fig_00000100",
                           "image_path": f"data/{doc_id}/figures/fig_00000100.png"})
    pf.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr("src.ingest.figures.refresh_figures", lambda *a, **k: [])
    monkeypatch.setattr("src.anchor.chunker.chunk_and_anchor", lambda *a, **k: {})

    # 在读行与提交之间插队一次他人提交（推高 data_revision）
    real_rows = DocStore.block_rows
    raced = {"done": False}

    def racing_rows(self, *a, **kw):
        rows = real_rows(self, *a, **kw)
        if not raced["done"]:
            raced["done"] = True
            DocStore(self.layout, self.doc_id).update_block(
                other["id"], {"title": "乙·并发改"}, expected_revision=None, author="human")
        return rows

    monkeypatch.setattr(DocStore, "block_rows", racing_rows)
    app = create_app(layout.root)
    token = app.state.app_state.token_value
    with TestClient(app, base_url="http://127.0.0.1:8787") as c:
        c.headers.update({"X-Local-Token": token})
        r = c.post(f"/api/v1/docs/{doc_id}/refresh-figures", json={})
        assert r.status_code == 200, r.text
        assert r.json()["patched_blocks"] == 1, r.json()

    monkeypatch.setattr(DocStore, "block_rows", real_rows)
    fresh = DocStore(layout, doc_id)
    assert fresh.get_block(other["id"])["title"] == "乙·并发改", "并发改动不得被静默冲掉"
    fixed = fresh.get_block(next(b["id"] for b in fresh.block_rows(ch)
                                 if b.get("title") == "图引修复"))
    assert "![fig_00000100]" in fixed["content"], fixed["content"]
