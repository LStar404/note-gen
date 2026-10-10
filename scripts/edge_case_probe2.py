"""边界情况探测（第二轮）：agent 编辑 / 备份恢复 / 任务 / 检索兜底 / 数据约束。"""

from __future__ import annotations

import io
import json
import sys
import time
import uuid
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from src.api.main import create_app

ROOT = Path(__file__).resolve().parents[1]
results = []


def record(name: str, ok: bool, detail: str):
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'} | {name} | {detail[:160]}")


def wait_task(c, tid, timeout=60):
    t = {"status": "n/a"}
    for _ in range(timeout * 2):
        t = c.get(f"/api/v1/tasks/{tid}").json()
        if t.get("status") in ("succeeded", "failed", "partial", "cancelled"):
            break
        time.sleep(0.5)
    return t


def main() -> int:
    app = create_app(ROOT)
    token = app.state.app_state.token_value
    c = TestClient(app, base_base := "http://127.0.0.1:8787") if False else TestClient(app, base_url="http://127.0.0.1:8787")
    c.headers.update({"X-Local-Token": token})

    # ---- 1) agent 编辑：无 tools 能力模型 ----
    r = c.post("/api/v1/blocks/b_zvpEy5riigPF/agent-edit",
               json={"mode": "plan", "instruction": "改写这一块"})
    record("agent 缺 tools 能力有明确错误",
           r.status_code in (400, 422) and "tools" in r.text.lower(),
           f"{r.status_code} {r.text[:110]}")

    # ---- 2) agent exec 未知 plan ----
    r = c.post("/api/v1/blocks/b_zvpEy5riigPF/agent-edit",
               json={"mode": "exec", "plan_id": "p_unknown", "confirmed": True})
    record("未知 plan 返回 404", r.status_code == 404, f"{r.status_code} {r.text[:90]}")

    cur = c.get("/api/v1/blocks/b_zvpEy5riigPF").json()["block"]["revision"]

    # ---- 3) 允许再生成缺 confirmed ----
    r = c.post("/api/v1/blocks/b_zvpEy5riigPF/allow-regeneration", json={"expected_revision": cur})
    record("allow-regeneration 需 confirmed", r.status_code in (400, 422), f"{r.status_code} {r.text[:90]}")

    # ---- 4) 重排到不存在的块 ----
    r = c.post("/api/v1/docs/demo-cli/chapters/ch01/reorder",
               json={"block_id": "b_zvpEy5riigPF", "after": "b_nothinghere1", "expected_revision": cur})
    record("重排到不存在块被拒", r.status_code in (400, 404), f"{r.status_code} {r.text[:90]}")

    # ---- 5) content 超 50KB / title 超 80 字 ----
    r = c.patch("/api/v1/blocks/b_zvpEy5riigPF",
                json={"content": "x" * (50 * 1024 + 1), "expected_revision": cur})
    record("content 超 50KB 被拒", r.status_code == 422, f"{r.status_code} {r.text[:90]}")
    r = c.patch("/api/v1/blocks/b_zvpEy5riigPF",
                json={"title": "标" * 81, "expected_revision": cur})
    record("title 超 80 字被拒", r.status_code == 422, f"{r.status_code} {r.text[:90]}")

    # ---- 6) 检索 mode=vec（无向量时回落 FTS）----
    r = c.get("/api/v1/search?doc_id=demo-cli&q=determinant&mode=vec")
    record("vec 检索无向量时回落", r.status_code == 200, f"{r.status_code} {r.text[:90]}")

    # ---- 7) 任务取消 / 不存在的任务 / 不存在的产物 ----
    r = c.get("/api/v1/tasks/task_nope")
    record("不存在任务 404", r.status_code == 404, str(r.status_code))
    r = c.get("/api/v1/tasks/task_nope/artifacts/x.md")
    record("不存在产物 404", r.status_code == 404, str(r.status_code))

    # ---- 8) 模型路由：不存在的模型 / agent 无 tools ----
    r = c.put("/api/v1/model-routes", json={"fill": "no-such/model"})
    record("路由到不存在模型被拒", r.status_code in (400, 404), f"{r.status_code} {r.text[:90]}")

    # ---- 9) 备份 / 恢复（CLI 级）----
    import subprocess
    p = subprocess.run([sys.executable, "cli.py", "backup", "demo-cli", "--out", "backups"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       cwd=str(ROOT), timeout=120)
    zips = sorted((ROOT / "backups").glob("demo-cli-*.zip"))
    record("备份生成", p.returncode == 0 and zips, f"rc={p.returncode} files={len(zips)}")
    if zips:
        # 恢复到同名文档（应拒绝）
        q = subprocess.run([sys.executable, "cli.py", "restore", str(zips[-1])],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           cwd=str(ROOT), timeout=120)
        record("恢复同名文档被拒", q.returncode == 1 and "已存在" in (q.stdout + q.stderr),
               f"rc={q.returncode} {(q.stdout + q.stderr)[-110:]}")

        # Zip Slip 拒绝
        evil = ROOT / "backups" / "evil.zip"
        with zipfile.ZipFile(evil, "w") as zf:
            zf.writestr("../evil.txt", "boom")
            zf.writestr("checksums.json", "{}")
            zf.writestr("backup.json", json.dumps({"doc_id": "evil-doc"}))
        q = subprocess.run([sys.executable, "cli.py", "restore", str(evil)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           cwd=str(ROOT), timeout=120)
        record("Zip Slip 被拒", q.returncode != 0 and "Zip Slip" in (q.stdout + q.stderr),
               f"rc={q.returncode} {(q.stdout + q.stderr)[-110:]}")

    # ---- 10) paper profile 导入（应单章）----
    pdf = (ROOT / "tests" / "golden" / "layout-plain.pdf").read_bytes() + b"\n%probe2-" + uuid.uuid4().bytes
    r = c.post("/api/v1/docs", files={"file": ("paper.pdf", pdf, "application/pdf")},
               data={"profile": "paper"})
    tid = r.json().get("task_id")
    t = wait_task(c, tid) if tid else {"status": f"http-{r.status_code}"}
    doc_id = (r.json() or {}).get("doc_id")
    chapters = []
    if t.get("status") == "succeeded":
        chapters = c.get(f"/api/v1/docs/{doc_id}").json().get("chapters", [])
    record("paper profile 固定单章",
           t.get("status") == "succeeded" and len(chapters) == 1,
           f"status={t.get('status')} chapters={len(chapters)}")

    # ---- 11) 超长内容/异常数值 order ----
    r = c.post("/api/v1/docs/demo-cli/chapters/ch01/blocks",
               json={"type": "motivation", "title": "t", "content": "c", "sources": [],
                     "basis": "synthesis", "order": "abc"})
    record("非法 order 被拒", r.status_code == 422, f"{r.status_code} {r.text[:90]}")

    # ---- 12) 用量与设置读取 ----
    r = c.get("/api/v1/usage")
    record("用量接口可用", r.status_code == 200 and "rows" in r.json(), str(r.status_code))

    print("\n==== summary ====")
    bad = [n for n, ok, _ in results if not ok]
    print(f"{len(results) - len(bad)}/{len(results)} 通过")
    for n in bad:
        print("需处理:", n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
