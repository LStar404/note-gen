"""未配置模型时生成必须清晰失败（probe1 抓到的假成功回归）。"""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from src.api.main import create_app


def _wait(c, tid, budget=180.0):
    """等任务到终态；预算放宽到 180s——整套并跑时导入解析（auto→marker 兜底）会慢很多。"""
    t = {"status": "n/a"}
    deadline = time.time() + budget
    while time.time() < deadline:
        t = c.get(f"/api/v1/tasks/{tid}").json()
        if t["status"] in ("succeeded", "failed", "partial", "cancelled", "interrupted"):
            return t
        time.sleep(0.25)
    return t


def test_generate_without_model_fails_clearly(workspace):
    """没有配置生成模型时，生成任务要 failed 并带「未配置生成模型」——不得静默成功。"""
    from tests.fixtures.make_sample_pdf import build_sample_pdf

    pdf = build_sample_pdf(workspace.root / "s.pdf")
    app = create_app(workspace.root)
    token = app.state.app_state.token_value
    with TestClient(app, base_url="http://127.0.0.1:8787") as c:
        c.headers.update({"X-Local-Token": token})
        with pdf.open("rb") as fh:
            r = c.post("/api/v1/docs", files={"file": ("s.pdf", fh.read(), "application/pdf")},
                       data={"doc_id": "no-model-doc"})
        assert _wait(c, r.json()["task_id"])["status"] == "succeeded", "导入应成功"

        g = c.post("/api/v1/docs/no-model-doc/generate", json={})
        assert g.status_code == 202, g.text
        t = _wait(c, g.json()["task_id"])
        assert t["status"] == "failed", t
        assert "未配置生成模型" in (t.get("error") or {}).get("message", "")
