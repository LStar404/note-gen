"""边界情况探测（第三轮）：并发/游标/Unicode/未知字段/配额与工具链。"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from src.api.main import create_app

ROOT = Path(__file__).resolve().parents[1]
results = []


def record(name: str, ok: bool, detail: str):
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'} | {name} | {detail[:150]}")


def wait_task(c, tid, timeout=60):
    t = {"status": "n/a"}
    for _ in range(timeout * 2):
        t = c.get(f"/api/v1/tasks/{tid}").json()
        if t.get("status") in ("succeeded", "failed", "partial", "cancelled", "interrupted"):
            break
        time.sleep(0.5)
    return t


def main() -> int:
    app = create_app(ROOT)
    token = app.state.app_state.token_value
    c = TestClient(app, base_url="http://127.0.0.1:8787")
    c.headers.update({"X-Local-Token": token})

    doc = "demo-cli"
    ch = "ch01"

    # ---- 1) 游标分页：数据变化后旧游标应 409 ----
    r1 = c.get(f"/api/v1/docs/{doc}/chapters/{ch}/blocks?limit=1")
    body = r1.json()
    cur = body.get("next_cursor")
    r2 = c.post(f"/api/v1/docs/{doc}/chapters/{ch}/blocks",
                json={"type": "motivation", "title": "游标测试", "content": "x",
                      "sources": [], "basis": "synthesis", "order": 5.0})
    r3 = c.get(f"/api/v1/docs/{doc}/chapters/{ch}/blocks?limit=1&cursor={cur}") if cur else None
    record("陈旧游标返回 409", r3 is not None and r3.status_code == 409,
           f"{r3.status_code if r3 else 'no-cursor'} {r3.text[:80] if r3 else ''}")

    # ---- 2) Unicode / emoji 内容与标题 ----
    r = c.post(f"/api/v1/docs/{doc}/chapters/{ch}/blocks",
               json={"type": "motivation", "title": "🎉 多语言 título ünïcode", "content": "日本語・한국어・emoji 🚀",
                     "sources": [], "basis": "synthesis", "order": 6.0})
    record("Unicode 内容可保存", r.status_code == 200 and "🚀" in r.json().get("content", ""),
           f"{r.status_code} {r.text[:80]}")

    # ---- 3) 空白标题 ----
    r = c.post(f"/api/v1/docs/{doc}/chapters/{ch}/blocks",
               json={"type": "motivation", "title": "   ", "content": "x",
                     "sources": [], "basis": "synthesis", "order": 7.0})
    record("空白标题被拒", r.status_code == 422, f"{r.status_code} {r.text[:80]}")

    # ---- 4) 未知字段：存储中的未知字段必须在读改写后保留（规范 4.2）----
    blk = c.get(f"/api/v1/docs/{doc}/chapters/{ch}/blocks").json()["items"][0]
    r = c.patch(f"/api/v1/blocks/{blk['id']}",
                json={"content": blk["content"], "expected_revision": blk["revision"],
                      "unknown_field": "应被忽略", "message": "未知字段测试"})
    from src.core.store import DocStore as _DS
    from src.core.paths import Layout as _L
    after = _DS(_L(ROOT), doc).get_block(blk["id"])
    record("未知输入字段被忽略且不报错", r.status_code == 200 and "unknown_field" not in after,
           f"{r.status_code} stored_extra={'unknown_field' in after}")

    # ---- 5) 重复来源锚点 ----
    from src.core.store import DocStore
    from src.core.paths import Layout
    s = DocStore(Layout(ROOT), doc)
    aid = s.anchor_rows(ch)[1]["anchor_id"]
    quote = s.anchors_by_id()[aid]["content"][:10]
    r = c.post(f"/api/v1/docs/{doc}/chapters/{ch}/blocks",
               json={"type": "definition", "title": "重复来源", "content": "c",
                     "sources": [{"anchor_id": aid, "quote": quote, "relation": "defines"},
                                 {"anchor_id": aid, "quote": quote, "relation": "states"}],
                     "order": 8.0})
    record("重复来源锚点被拒", r.status_code == 422, f"{r.status_code} {r.text[:90]}")

    # ---- 6) 外部 URL 链接允许 ----
    blk = c.get(f"/api/v1/docs/{doc}/chapters/{ch}/blocks").json()["items"][0]
    r = c.patch(f"/api/v1/blocks/{blk['id']}",
                json={"links": ["https://example.com/ref"], "expected_revision": blk["revision"],
                      "message": "外链测试"})
    record("外部 URL 链接允许", r.status_code == 200 and "https://example.com/ref" in r.json().get("links", []),
           f"{r.status_code} {r.text[:80]}")

    # ---- 7) textbook profile 拒绝论文块类型 ----
    r = c.post(f"/api/v1/docs/{doc}/chapters/{ch}/blocks",
               json={"type": "contribution", "title": "贡献", "content": "c",
                     "sources": [{"anchor_id": aid, "quote": quote, "relation": "summarizes"}], "order": 9.0})
    record("textbook 拒绝论文块类型", r.status_code == 422, f"{r.status_code} {r.text[:90]}")

    # ---- 8) 并发生成任务（同一 doc 两次触发）----
    r1 = c.post(f"/api/v1/docs/{doc}/generate", json={"chapter_id": ch})
    r2 = c.post(f"/api/v1/docs/{doc}/generate", json={"chapter_id": ch})
    t1 = wait_task(c, r1.json()["task_id"], 30)
    t2 = wait_task(c, r2.json()["task_id"], 30)
    record("并发生成不崩溃",
           t1.get("status") in ("succeeded", "failed", "partial") and
           t2.get("status") in ("succeeded", "failed", "partial"),
           f"1st={t1.get('status')} 2nd={t2.get('status')}")

    # ---- 9) eval 缺少用例文件 ----
    import subprocess
    q = subprocess.run([sys.executable, "cli.py", "eval", "no-such-doc", "--cases", "tests/eval/missing.jsonl"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       cwd=str(ROOT), timeout=60)
    record("eval 缺用例有清晰错误", q.returncode == 2 and "缺少" in (q.stdout + q.stderr),
           f"rc={q.returncode} {(q.stdout + q.stderr)[-90:]}")

    # ---- 10) export --out 到非法路径 ----
    q = subprocess.run([sys.executable, "cli.py", "export", doc, "--chapter", ch,
                        "--out", "Z:\\nonexistent-drive\\out"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       cwd=str(ROOT), timeout=60)
    record("非法导出路径有可解释错误", q.returncode != 0 and bool((q.stdout + q.stderr).strip()),
           f"rc={q.returncode} {(q.stdout + q.stderr)[-90:]}")

    # ---- 11) 大批量块列表性能（<1s）----
    start = time.perf_counter()
    r = c.get(f"/api/v1/docs/{doc}/chapters/{ch}/blocks?limit=200")
    dt = time.perf_counter() - start
    record("块列表 200 条 < 1s", r.status_code == 200 and dt < 1.0,
           f"{dt:.3f}s items={len(r.json().get('items', []))}")

    # ---- 12) 锚点/块点查性能（<100ms）----
    from src.core.store import DocStore
    s = DocStore(Layout(ROOT), doc)
    bid = s.all_block_rows()[0]["id"] if s.all_block_rows() else None
    if bid:
        start = time.perf_counter()
        r = c.get(f"/api/v1/blocks/{bid}")
        dt = time.perf_counter() - start
        record("块点查 < 100ms", r.status_code == 200 and dt < 0.1, f"{dt*1000:.0f}ms")
    start = time.perf_counter()
    r = c.get(f"/api/v1/anchors/{aid}")
    dt = time.perf_counter() - start
    record("锚点点查 < 100ms", r.status_code == 200 and dt < 0.1, f"{dt*1000:.0f}ms")

    print("\n==== summary ====")
    bad = [n for n, ok, _ in results if not ok]
    print(f"{len(results) - len(bad)}/{len(results)} 通过")
    for n in bad:
        print("需处理:", n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
