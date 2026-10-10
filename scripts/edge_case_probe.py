"""边界情况探测：把意料之外的输入跑一遍，记录真实行为（供自查与回归）。"""

from __future__ import annotations

import sys
import time
import uuid
from pathlib import Path


def unique_pdf(raw: bytes) -> bytes:
    """每次探测用不同内容，避免撞上“文档已存在”守卫（探测器自身要幂等）。"""
    return raw + b"\n%probe-" + uuid.uuid4().bytes

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from src.api.main import create_app

ROOT = Path(__file__).resolve().parents[1]
results = []


def record(name: str, ok: bool, detail: str):
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'} | {name} | {detail[:150]}")


def main() -> int:
    app = create_app(ROOT)
    token = app.state.app_state.token_value
    c = TestClient(app, base_url="http://127.0.0.1:8787")
    c.headers.update({"X-Local-Token": token})

    # 1) 非 PDF 文件上传
    r = c.post("/api/v1/docs", files={"file": ("note.txt", b"hello", "text/plain")})
    record("上传非 PDF 被拒", r.status_code in (400, 422), f"{r.status_code} {r.text[:80]}")

    # 2) 损坏的 PDF
    r = c.post("/api/v1/docs", files={"file": ("bad.pdf", b"not a pdf at all", "application/pdf")})
    record("损坏 PDF 返回可解释错误", r.status_code in (200, 202, 400, 422), f"{r.status_code} {r.text[:100]}")
    if r.status_code == 202:
        tid = r.json()["task_id"]
        for _ in range(60):
            t = c.get(f"/api/v1/tasks/{tid}").json()
            if t["status"] in ("succeeded", "failed", "partial"):
                break
            time.sleep(0.5)
        record("损坏 PDF 任务状态", t["status"] == "failed",
               f"status={t['status']} err={(t.get('error') or {}).get('message', '')[:100]}")

    # 3) 空内容 PDF（无文字层、无图）
    tmp = ROOT / "data" / "_probe_blank.pdf"
    import pypdf
    w = pypdf.PdfWriter()
    w.add_blank_page(width=612, height=792)
    with tmp.open("wb") as fh:
        w.write(fh)
    r = c.post("/api/v1/docs", files={"file": ("blank.pdf", unique_pdf(tmp.read_bytes()), "application/pdf")})
    if r.status_code == 202:
        tid = r.json()["task_id"]
        for _ in range(90):
            t = c.get(f"/api/v1/tasks/{tid}").json()
            if t["status"] in ("succeeded", "failed", "partial"):
                break
            time.sleep(0.5)
        record("空白 PDF 导入不崩溃", t["status"] in ("succeeded", "partial"),
               f"status={t['status']} err={(t.get('error') or {}).get('message', '')[:100]}")
    else:
        record("空白 PDF 导入不崩溃", False, f"{r.status_code} {r.text[:100]}")

    # 4) 不存在的文档/块/锚点
    r = c.get("/api/v1/docs/no-such-doc")
    record("不存在文档返回 404", r.status_code == 404, str(r.status_code))
    r = c.get("/api/v1/blocks/b_AAAAAAAAAAAA")
    record("不存在块返回 404", r.status_code == 404, str(r.status_code))
    r = c.get("/api/v1/anchors/a_12345678_1234567890abcdef")
    record("不存在锚点返回 404", r.status_code == 404, str(r.status_code))

    # 5) 非法请求体
    r = c.post("/api/v1/docs/sample-la/match", json={"page": 0, "rects": []})
    record("空选区返回 400", r.status_code == 400, f"{r.status_code} {r.text[:80]}")
    r = c.post("/api/v1/docs/sample-la/chapters/ch01/blocks",
               json={"type": "definition", "title": "x", "content": "y", "order": 1.0,
                     "sources": [{"anchor_id": "a_00000000_" + "0" * 16, "quote": "q", "relation": "defines"}]})
    record("非法来源锚点被拒", r.status_code in (400, 422), f"{r.status_code} {r.text[:100]}")

    # 6) 乐观锁/回滚到不存在版本
    r = c.post("/api/v1/blocks/b_zvpEy5riigPF/rollback", json={"rev_id": "b_zvpEy5riigPF@r99"})
    record("回滚到不存在版本返回 404", r.status_code in (404, 409), f"{r.status_code} {r.text[:100]}")

    # 7) 生成：未配置模型
    r = c.post("/api/v1/docs/sample-la/generate", json={"chapter_id": "ch01"})
    tid = r.json().get("task_id") if r.status_code == 202 else None
    if tid:
        for _ in range(30):
            t = c.get(f"/api/v1/tasks/{tid}").json()
            if t["status"] in ("succeeded", "failed", "partial"):
                break
            time.sleep(0.5)
        record("未配置模型时生成有清晰错误",
               t["status"] == "failed" and bool((t.get("error") or {}).get("message")),
               f"status={t['status']} err={(t.get('error') or {}).get('message', '')[:100]}")
    else:
        record("未配置模型时生成有清晰错误", False, f"{r.status_code} {r.text[:100]}")

    # 8) 重复导入（同名文档）
    pdf = unique_pdf((ROOT / "tests" / "golden" / "sample.pdf").read_bytes())
    r1 = c.post("/api/v1/docs", files={"file": ("sample.pdf", pdf, "application/pdf")})
    tid = r1.json().get("task_id")
    t = {"status": "n/a"}
    for _ in range(120):
        t = c.get(f"/api/v1/tasks/{tid}").json()
        if t.get("status") in ("succeeded", "failed", "partial"):
            break
        time.sleep(0.5)
    r2 = c.post("/api/v1/docs", files={"file": ("sample.pdf", pdf, "application/pdf")})
    if r2.status_code >= 400:
        record("重复导入被显式拒绝", r2.status_code == 409,
               f"1st={t['status']} 2nd=HTTP {r2.status_code} {r2.text[:90]}")
    else:
        tid2 = r2.json().get("task_id")
        t2 = {"status": "n/a"}
        for _ in range(120):
            t2 = c.get(f"/api/v1/tasks/{tid2}").json()
            if t2.get("status") in ("succeeded", "failed", "partial"):
                break
            time.sleep(0.5)
        record("重复导入行为可解释",
               t2.get("status") in ("failed", "succeeded"),
               f"1st={t.get('status')} 2nd={t2.get('status')} err={(t2.get('error') or {}).get('message', '')[:80]}")

    # 9) 检索空查询 / 越界分页
    r = c.get("/api/v1/search?doc_id=sample-la&q=")
    record("空查询不崩溃", r.status_code in (200, 400), str(r.status_code))
    r = c.get("/api/v1/docs/sample-la/chapters/ch01/blocks?limit=999")
    record("超大 limit 被夹取", r.status_code == 200 and len(r.json().get("items", [])) <= 200,
           f"{r.status_code} items={len(r.json().get('items', []))}")

    # 10) 导出空章节
    r = c.post("/api/v1/export", json={"doc_id": "sample-la", "chapter_id": "ch01", "format": "tex"})
    tid = r.json().get("task_id")
    for _ in range(60):
        t = c.get(f"/api/v1/tasks/{tid}").json()
        if t["status"] in ("succeeded", "failed", "partial"):
            break
        time.sleep(0.5)
    record("空章节导出不崩溃", t["status"] == "succeeded",
           f"status={t['status']} err={(t.get('error') or {}).get('message', '')[:80]}")

    print("\n==== summary ====")
    bad = [n for n, ok, _ in results if not ok]
    print(f"{len(results) - len(bad)}/{len(results)} 通过")
    for n in bad:
        print("需处理:", n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
