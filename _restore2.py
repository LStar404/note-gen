
import json, glob, os, shutil, collections, re

src = "data/.trash/doc-a016ba58d56d-20261011T050235Z"
dst = "data/doc-a016ba58d56d"
assert not os.path.exists(dst), "目标已存在，停止"
shutil.move(src, dst)
print("已恢复:", dst)

rows = sum(1 for f in glob.glob(os.path.join(dst, "blocks", "*.jsonl")) for _ in open(f, encoding="utf-8"))
anchors = sum(1 for f in glob.glob(os.path.join(dst, "anchors", "*.jsonl")) for _ in open(f, encoding="utf-8"))
print(f"块 {rows} | 锚点 {anchors}")
print("目录内容:", sorted(os.listdir(dst)))

# 控制字符采样
CTRL = [0x00, 0x08, 0x0b, 0x0c] + list(range(0x0e, 0x20))
cnt = collections.Counter()
samples = []
for f in glob.glob(os.path.join(dst, "anchors", "*.jsonl")):
    for line in open(f, encoding="utf-8"):
        a = json.loads(line)
        c = a.get("content") or ""
        for ch in c:
            if ord(ch) in CTRL:
                cnt[hex(ord(ch))] += 1
                if len(samples) < 6:
                    i = c.index(ch)
                    samples.append((hex(ord(ch)), repr(c[max(0, i-35):i+35])))
print("控制字符分布:", dict(cnt))
for s in samples:
    print("   ", s[0], s[1])
