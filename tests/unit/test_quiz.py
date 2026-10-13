"""AI 出题回归：题库入库、出题产物形状、API 往返。"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from src.api.main import create_app
from src.core.store import DocStore
from src.generate.quiz import append_qbank, extract_qbank_entries, generate_quiz


class QuizClient:
    """记录调用并返回合法题集的假 LLM。"""

    def __init__(self):
        self.calls = []

    def route(self, task):
        return "fake/fake"

    def complete(self, task, messages, **kw):
        self.calls.append({"task": task, "schema": kw.get("schema")})
        return {"output": {"questions": [
            {"id": "q1", "type": "choice", "question": "函数 f 在全纯区域的积分等于什么？",
             "options": ["0", "留数和", "无穷大", "与路径无关但非零"], "answer": "留数和",
             "explanation": "留数定理"},
            {"id": "q2", "type": "short", "question": "简述解析延拓的唯一性。",
             "answer": "由幂级数展开唯一确定", "explanation": "恒等定理"},
        ]}, "usage": {}}


def test_qbank_extraction_and_append(tmp_path):
    entries = extract_qbank_entries("past.pdf",
        "1. 计算下列极限并说明理由，写出完整推导过程。\n\n"
        "这道简答题要求解释一致收敛与内闭一致收敛的区别与联系。\n\n短")
    assert len(entries) == 2, entries
    added = append_qbank(tmp_path, entries)
    assert added == 2
    assert append_qbank(tmp_path, entries) == 0, "重复入库去重"


def _seed_block(store, ch: str):
    """出题的前提是已有笔记（生成未跑时夹具里只有锚点没有块）——测试先造笔记。"""
    return store.create_block({"type": "motivation", "title": "留数定理",
                               "content": "若 f 在闭曲线内部除有限个孤立奇点外全纯，"
                                          "则沿闭曲线的积分等于 2πi 乘以留数之和。",
                               "sources": [], "basis": "synthesis", "order": 1.0},
                              author="human", chapter_id=ch)


def test_generate_quiz_shapes_and_saves(sample_doc):
    layout, doc_id = sample_doc
    store = DocStore(layout, doc_id)
    ch = store.chapter_ids()[0]
    _seed_block(store, ch)
    client = QuizClient()
    quiz = generate_quiz(client, store=store, chapter_ids=[ch],
                         kinds=["choice", "short"], count=2, difficulty="期末水平",
                         model="fake/fake", config={"generate": {}},
                         qbank=[{"text": "往年题：计算曲线积分并证明收敛性（风格样本）"}])
    assert quiz["questions"][0]["type"] == "choice"
    assert quiz["questions"][0]["options"], "选择题必须带选项"
    assert quiz["questions"][1]["answer"]
    saved = (layout.doc_dir(doc_id) / "quiz" / f"{quiz['quiz_id']}.json")
    assert saved.exists()
    assert json.loads(saved.read_text(encoding="utf-8"))["count"] == 2
    assert client.calls[0]["task"] == "quiz"
    assert client.calls[0]["schema"], "出题必须带结构化 schema"


def test_quiz_and_qbank_api_roundtrip(sample_doc):
    layout, doc_id = sample_doc
    _seed_block(DocStore(layout, doc_id), DocStore(layout, doc_id).chapter_ids()[0])
    app = create_app(layout.root)
    with TestClient(app, base_url="http://127.0.0.1:8787") as client:
        headers = {"X-Local-Token": app.state.app_state.token_value}
        import io

        r = client.post(f"/api/v1/docs/{doc_id}/qbank",
                        files={"file": ("past.txt", io.BytesIO(
                            "往年题一：计算下列积分并说明理由，要求写出完整的解题步骤。".encode("utf-8")),
                            "text/plain")},
                        headers=headers)
        assert r.status_code == 200, r.text
        assert r.json()["added"] == 1
        r = client.get(f"/api/v1/docs/{doc_id}/qbank", headers=headers)
        assert r.json()["count"] == 1
        # 出题：用假 LLM 替换 st.client
        app.state.app_state.client = QuizClient()
        r = client.post(f"/api/v1/docs/{doc_id}/quiz",
                        json={"kinds": ["choice", "short"], "count": 2},
                        headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["questions"] and body["questions"][0]["question"]
