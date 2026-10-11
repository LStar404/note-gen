"""图资源刷新回归：无图时优雅返回；id/图注规则稳定。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.api.main import create_app
from src.ingest.figures import _caption_for, _stable_fig_id


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
