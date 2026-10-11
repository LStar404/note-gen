"""生成记录 API 回归：GET generate-reports 返回各章生成报告（UI 生成记录弹窗数据源）。"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from src.api.main import create_app


def test_generate_reports_lists_chapter_reports(sample_doc):
    layout, doc_id = sample_doc
    # 造两份报告（一份正常一份损坏，验证损坏不拖垮列表）
    d = layout.reports_doc_dir(doc_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "ch01.generate.json").write_text(json.dumps({
        "doc_id": doc_id, "chapter_id": "ch01", "status": "partial",
        "created_at": "2026-10-10T09:52:30Z",
        "model": {"outline": "mimo/a", "fill": "mimo/b"},
        "created": ["b1"], "updated": [], "failures": [{"temp_id": "t1", "reason": "LLM 调用失败"}],
    }), encoding="utf-8")
    (d / "ch02.generate.json").write_text("{broken", encoding="utf-8")

    app = create_app(layout.root)
    token = app.state.app_state.token_value
    with TestClient(app, base_url="http://127.0.0.1:8787") as c:
        c.headers.update({"X-Local-Token": token})
        r = c.get(f"/api/v1/docs/{doc_id}/generate-reports")
        assert r.status_code == 200, r.text
        reports = r.json()["reports"]
        assert len(reports) == 1, reports          # 损坏报告被跳过
        rep = reports[0]
        assert rep["chapter_id"] == "ch01"
        assert rep["status"] == "partial"
        assert rep["model"]["fill"] == "mimo/b"
        assert len(rep["created"]) == 1
        assert rep["failures"][0]["reason"] == "LLM 调用失败"

        # 未知文档 → 404
        r2 = c.get("/api/v1/docs/no-such-doc/generate-reports")
        assert r2.status_code == 404, r2.text


def test_generate_reports_empty_for_fresh_doc(sample_doc):
    layout, doc_id = sample_doc
    app = create_app(layout.root)
    token = app.state.app_state.token_value
    with TestClient(app, base_url="http://127.0.0.1:8787") as c:
        c.headers.update({"X-Local-Token": token})
        r = c.get(f"/api/v1/docs/{doc_id}/generate-reports")
        assert r.status_code == 200
        assert r.json()["reports"] == []
