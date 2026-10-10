"""底层基准：JSON 序列化（stdlib vs orjson/Rust）与 SQLite 写入方式对比。"""

from __future__ import annotations

import glob
import json
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import orjson

NL = bytes([10])


def bench(name, fn, n=3):
    ts = []
    for _ in range(n):
        t = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t) * 1000)
    print(f"{name:34s} {min(ts):8.1f} ms  (min of {n})")


files = sorted(glob.glob("data/doc-a016ba58d56d/anchors/*.jsonl"))
raw = b"".join(Path(f).read_bytes() for f in files)
lines = [l for l in raw.split(NL) if l.strip()]
print(f"锚点数据: {len(files)} 文件 / {len(lines)} 行 / {len(raw)/1e6:.2f} MB")

bench("stdlib json.loads 逐行", lambda: [json.loads(l) for l in lines])
bench("orjson.loads 逐行", lambda: [orjson.loads(l) for l in lines])
rows = [json.loads(l) for l in lines]
bench("stdlib json.dumps 逐行", lambda: [json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in rows])
bench("orjson.dumps 逐行", lambda: [orjson.dumps(r) for r in rows])

tpl = []
for r in rows[:2000]:
    tpl.append((r["anchor_id"], "doc", r["chapter_id"], r["page"],
                r["bbox"][0], r["bbox"][1], r["bbox"][2], r["bbox"][3],
                r["content_type"], r["reading_order"], r.get("heading_level", 0), r["content"]))

DDL = ("CREATE TABLE IF NOT EXISTS anchors (anchor_id TEXT PRIMARY KEY, doc_id TEXT,"
       " chapter_id TEXT, page INTEGER, bbox_x0 REAL, bbox_y0 REAL, bbox_x1 REAL,"
       " bbox_y1 REAL, content_type TEXT, reading_order INTEGER, heading_level INTEGER,"
       " content TEXT)")
INSERT = "INSERT OR REPLACE INTO anchors VALUES(?,?,?,?,?,?,?,?,?,?,?,?)"


def mk():
    d = Path(tempfile.mkdtemp())
    c = sqlite3.connect(str(d / "t.db"))
    c.execute(DDL)
    return c


def one_by_one():
    c = mk()
    with c:
        for t in tpl:
            c.execute(INSERT, t)
    c.close()


def batched():
    c = mk()
    with c:
        c.executemany(INSERT, tpl)
    c.close()


bench("SQLite 逐条 execute", one_by_one)
bench("SQLite executemany 批量", batched)
