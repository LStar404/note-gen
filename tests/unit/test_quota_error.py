"""配额用尽（402 / 429+quota）必须与限流区分：不重试、快速失败。
配额错误按块隔离为失败结果（已完成块落缓存、下次生成续填），
由 LLMClient 熔断保证不逐块白试上游 API。"""

from __future__ import annotations

import json

import pytest

from src.core.errors import QuotaExhausted, RetryableError
from src.core.llm import OpenAICompatAdapter
from src.generate.fill import fill_one


class _FakeResp:
    def __init__(self, status_code: int, text: str):
        self.status_code = status_code
        self.text = text

    def json(self):
        return json.loads(self.text)


def _call(monkeypatch, resp):
    import httpx

    monkeypatch.setattr(httpx, "post", lambda *a, **k: resp)
    adapter = OpenAICompatAdapter("mimo", "https://example.invalid/v1", "k", timeout=5)
    return adapter.complete("mimo-v2.6-flash", [{"role": "user", "content": "hi"}], None, 8)


def test_quota_exhausted_is_not_retryable(monkeypatch):
    resp = _FakeResp(429, '{"error":{"code":"429","message":"quota exhausted","type":"limitation"}}')
    with pytest.raises(QuotaExhausted) as ei:
        _call(monkeypatch, resp)
    assert "配额已用尽" in str(ei.value)
    assert "quota exhausted" in str(ei.value), "必须把上游原话带给用户"


def test_rate_limit_still_retryable(monkeypatch):
    resp = _FakeResp(429, '{"error":{"message":"Rate limit exceeded, please retry later"}}')
    with pytest.raises(RetryableError):
        _call(monkeypatch, resp)


def test_402_is_quota(monkeypatch):
    resp = _FakeResp(402, '{"error":{"message":"Insufficient balance"}}')
    with pytest.raises(QuotaExhausted):
        _call(monkeypatch, resp)


def test_auth_error_carries_upstream_message(monkeypatch):
    from src.core.errors import AuthError

    resp = _FakeResp(401, '{"error":{"message":"Invalid API Key","type":"invalid_key"}}')
    with pytest.raises(AuthError) as ei:
        _call(monkeypatch, resp)
    assert "Invalid API Key" in str(ei.value)


def test_fill_one_isolates_quota_as_failed_result():
    """配额错误按块隔离为失败结果（保整章缓存、下次续填）；不白试由熔断保证。"""

    class Client:
        def complete(self, *a, **kw):
            raise QuotaExhausted("模型 mimo-v2.6-flash 配额已用尽")

    spec = {"temp_id": "t1", "type": "motivation", "title": "x", "basis": "synthesis",
            "sources": [], "depends_on": []}
    result = fill_one(Client(), doc_id="d", chapter_id="ch01", spec=spec, anchors_by_id={},
                      anchors=[], model=None, config={"generate": {}}, related_titles=[])
    assert result["_status"] == "failed", result
    assert any("配额已用尽" in e for e in result["_errors"]), result
