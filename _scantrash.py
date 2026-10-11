
import json, glob, re, collections
# 扫描回收站副本的伪影（决定是否需要重排）
SPACED = re.compile(r"(?:(?<=\s)|^)(?:[A-Za-z] ){3,}")
GARBLED = re.compile(r"[\u0980-\u09FF\u1000-\u109F\u13A0-\u13FF\u0E00-\u0E7F]")  # 孟加拉/缅甸/格鲁吉亚/泰文乱码
CTRL = [0x00, 0x08, 0x0b, 0x0c] + list(range(0x0e, 0x20))
n_a = a_spaced = a_garbled = a_ctrl = 0
for f in glob.glob(r"data/.trash/doc-a016ba58d56d-20261011T050235Z/anchors/*.jsonl"):
    for line in open(f, encoding="utf-8"):
        a = json.loads(line)
        c = a.get("content") or ""
        n_a += 1
        if SPACED.search(c): a_spaced += 1
        if GARBLED.search(c): a_garbled += 1
        if any(ord(ch) in CTRL for ch in c): a_ctrl += 1
print(f"锚点 {n_a} | 逐字母空格 {a_spaced} | 乱码 {a_garbled} | 控制字符 {a_ctrl}")
n_b = b_bad = 0
for f in glob.glob(r"data/.trash/doc-a016ba58d56d-20261011T050235Z/blocks/*.jsonl"):
    for line in open(f, encoding="utf-8"):
        b = json.loads(line)
        n_b += 1
        c = (b.get("content") or "")
        if SPACED.search(c) or GARBLED.search(c):
            b_bad += 1
print(f"块 {n_b} | 伪影 {b_bad}")
