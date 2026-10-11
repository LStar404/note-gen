
import json, glob, re
CTRL = [0x00, 0x08, 0x0b, 0x0c] + list(range(0x0e, 0x20))
# 1) 块 quotes 是否带字形垃圾
n_q = q_bad = 0
samples = []
for f in glob.glob(r"data/doc-a016ba58d56d/blocks/*.jsonl"):
    for line in open(f, encoding="utf-8"):
        b = json.loads(line)
        for s in b.get("sources") or []:
            n_q += 1
            q = s.get("quote") or ""
            if any(ord(c) in CTRL for c in q):
                q_bad += 1
                if len(samples) < 3:
                    samples.append(repr(q[:80]))
print(f"块 quotes {n_q} 条，带字形垃圾 {q_bad}")
for s in samples:
    print("   ", s)
# 2) 找一个受害页码
for f in glob.glob(r"data/doc-a016ba58d56d/anchors/*.jsonl"):
    for line in open(f, encoding="utf-8"):
        a = json.loads(line)
        c = a.get("content") or ""
        if "\x00bd" in c:
            print("受害锚点页:", a.get("page"), "| bbox:", [round(x) for x in (a.get("bbox") or [])])
            raise SystemExit
