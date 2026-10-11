"""生成任务结局回归：块级失败不得谎报 succeeded；单章失败不拖垮整本书。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.api.main import create_app
from src.core.errors import PipelineError


def _client(sample_doc):
    layout, doc_id = sample_doc
    app = create_app(layout.root)
    token = app.state.app_state.token_value
    c = TestClient(app, base_url="http://127.0.0.1:8787")
    c.headers.update({"X-Local-Token": token})
    return c, doc_id


def _wait(c, tid):
    import time

    t = {"status": "n/a"}
    for _ in range(120):
        t = c.get(f"/api/v1/tasks/{tid}").json()
        if t["status"] in ("succeeded", "failed", "partial", "cancelled", "interrupted"):
            return t
        time.sleep(0.25)
    return t


def _join(c, tid):
    th = None
    with c.app.state.app_state.tasks._lock:
        th = c.app.state.app_state.tasks._threads.get(tid)
    if th is not None:
        th.join(10)


def test_partial_chapter_report_marks_task_partial(sample_doc, monkeypatch):
    """章内块级失败（report status=partial）→ 任务结局 partial，不得谎报 succeeded。"""
    c, doc_id = _client(sample_doc)
    with c:
        def fake_generate(doc_id, chapter_id, **kw):
            return {"doc_id": doc_id, "chapter_id": chapter_id, "status": "partial",
                    "created": [], "updated": [], "skipped_protected": [],
                    "failures": [{"stage": "fill", "reason": "LLM 调用失败"}]}

        monkeypatch.setattr("src.generate.engine.generate_chapter", fake_generate)
        r = c.post(f"/api/v1/docs/{doc_id}/generate", json={"chapter_id": "ch01"})
        assert r.status_code == 202, r.text
        t = _wait(c, r.json()["task_id"])
        _join(c, r.json()["task_id"])
        assert t["status"] == "partial", t


def test_one_chapter_failure_does_not_kill_the_rest(sample_doc, monkeypatch):
    """单章抛 PipelineError 记录后继续其他章；部分成功 → partial 结局。"""
    c, doc_id = _client(sample_doc)
    with c:
        seen: list[str] = []

        def fake_generate(doc_id, chapter_id, **kw):
            seen.append(chapter_id)
            if chapter_id == "ch01":
                raise PipelineError("章节 ch01 没有锚点，请先运行 anchor")
            return {"doc_id": doc_id, "chapter_id": chapter_id, "status": "complete",
                    "created": [], "updated": [], "skipped_protected": [], "failures": []}

        monkeypatch.setattr("src.generate.engine.generate_chapter", fake_generate)
        r = c.post(f"/api/v1/docs/{doc_id}/generate", json={})
        assert r.status_code == 202, r.text
        t = _wait(c, r.json()["task_id"])
        _join(c, r.json()["task_id"])
        assert len(seen) >= 2, f"ch01 失败后其余章必须继续: {seen}"
        assert t["status"] == "partial", t
        reports = t["result"]["reports"]
        assert any(x["status"] == "failed" for x in reports)
        assert any(x["status"] == "complete" for x in reports)


def test_schema_failure_in_one_chapter_is_isolated(sample_doc, monkeypatch):
    """非 Pipeline/Conflict 的业务错误（如提交校验 SchemaViolation）同样只记本章，
    不得让后面章节失去机会（4.7 阶段性失败语义）。"""
    from src.core.errors import SchemaViolation

    c, doc_id = _client(sample_doc)
    with c:
        seen: list[str] = []

        def fake_generate(doc_id, chapter_id, **kw):
            seen.append(chapter_id)
            if chapter_id == "ch01":
                raise SchemaViolation("quote 不是锚点内容的连续子串")
            return {"doc_id": doc_id, "chapter_id": chapter_id, "status": "complete",
                    "created": [], "updated": [], "skipped_protected": [], "failures": []}

        monkeypatch.setattr("src.generate.engine.generate_chapter", fake_generate)
        r = c.post(f"/api/v1/docs/{doc_id}/generate", json={})
        assert r.status_code == 202, r.text
        t = _wait(c, r.json()["task_id"])
        _join(c, r.json()["task_id"])
        assert len(seen) >= 2, f"SchemaViolation 后其余章必须继续: {seen}"
        assert t["status"] == "partial", t
        reasons = str(t["result"]["reports"])
        assert "quote 不是锚点内容的连续子串" in reasons


def test_all_chapters_failed_marks_task_failed(sample_doc, monkeypatch):
    """全军覆没必须 failed 结局并带原因，不得亮绿灯（10.4）。"""
    c, doc_id = _client(sample_doc)
    with c:
        def fake_generate(doc_id, chapter_id, **kw):
            raise PipelineError(f"章节 {chapter_id} 没有锚点")

        monkeypatch.setattr("src.generate.engine.generate_chapter", fake_generate)
        r = c.post(f"/api/v1/docs/{doc_id}/generate", json={})
        assert r.status_code == 202, r.text
        t = _wait(c, r.json()["task_id"])
        _join(c, r.json()["task_id"])
        assert t["status"] == "failed", t
        assert "生成失败" in t["error"]["message"], t
