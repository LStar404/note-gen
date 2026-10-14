"""数据完整性体检喵（只读默认）：锚点身份 / 来源引用 / 引文一致性 / 索引同步。

用法：
  python scripts/integrity_report.py                     # 只报告
  python scripts/integrity_report.py --doc doc-xxxx      # 只查一本书
  python scripts/integrity_report.py --fix-quotes        # 预览引文修复
  python scripts/integrity_report.py --fix-quotes --apply  # 真正修复（走 CAS + 历史）

背景（实测）：
- 锚点 ID 应当是 base_anchor_id(doc, content) = 内容哈希；2026-10-12 导入的三本书
  存在历史遗留的「ID 与自身 text_hash 不符」（内容被换过、ID 没重算）。不影响使用：
  来源引用全部解析成功、text_hash 正确（再锚定时的迁移配对靠它），新导入的书 0 处。
- 引文必须是对应锚点内容的连续子串（提交时强制）。历史上个别活块的引文已不符，
  会导致这些块「改来源/回滚」被校验拒绝——本脚本可修复（取锚点内容里最接近的片段）。
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.core.paths import Layout                      # noqa: E402
from src.core.store import DocStore                     # noqa: E402
from src.core.text import normalize_text                # noqa: E402
from src.db.reindex import reindex_doc                  # noqa: E402
from src.ingest.vision_ocr import weak_ocr_text         # noqa: E402  复用弱识别启发式


def _suffix_stripped(tail: str) -> str:
    return re.sub(r"-\d+$", "", tail)


def anchor_id_mismatch(anchor: dict) -> bool:
    """锚点 ID 末尾哈希是否与自身 text_hash 不符（排除 -N 去重后缀）。"""
    cid = anchor.get("anchor_id") or ""
    if "_" not in cid:
        return False
    return _suffix_stripped(cid.split("_")[-1]) != (anchor.get("text_hash") or "")[:16]


def find_best_quote(anchor_content: str, old_quote: str, *, max_len: int = 80) -> str:
    """在锚点内容里找与旧引文最接近的真实片段（作为可校验的新引文）。

    用最长公共匹配定位：返回的片段必定是锚点内容的连续子串，长度受 max_len 约束。"""
    content = anchor_content or ""
    old = (old_quote or "").strip()
    if not content:
        return ""
    if old and normalize_text(old) in normalize_text(content):
        return old
    sm = difflib.SequenceMatcher(None, old or content[:max_len], content, autojunk=False)
    match = sm.find_longest_match(0, len(old or content[:max_len]), 0, len(content))
    if match.size <= 0:
        return content[:max_len].strip()
    start = match.b
    end = min(len(content), start + max(match.size, min(max_len, len(old) or match.size)))
    # 向两侧扩到词/句边界，读起来完整
    while start > 0 and content[start - 1] not in " \n\t。！？.;；：:，,":
        start -= 1
    while end < len(content) and content[end] not in " \n\t。！？.;；：:，,":
        end += 1
    return content[start:end].strip()[:max_len]


def scan_doc(doc: DocStore, *, verbose: bool = False) -> dict:
    """返回一本书的完整性统计与问题清单。"""
    anchors = doc.all_anchor_rows()
    amap = {a["anchor_id"]: a for a in anchors}
    stale_ids = sum(1 for a in anchors if anchor_id_mismatch(a))
    missing: list[tuple[str, str]] = []
    bad_quotes: list[tuple[str, str, str, str]] = []   # block_id, anchor_id, old, new
    garbled: list[tuple[str, str, str]] = []           # block_id, anchor_id, 锚点原文片段
    for ch in doc.manifest().get("chapters", []):
        for b in doc.block_rows(ch["chapter_id"]):
            for s in (b.get("sources") or []):
                aid = s.get("anchor_id")
                a = amap.get(aid)
                if a is None:
                    missing.append((b["id"], aid or ""))
                    continue
                q = (s.get("quote") or "").strip()
                content = a.get("content") or ""
                if q and normalize_text(q) not in normalize_text(content):
                    cand = find_best_quote(content, q)
                    # 判「将要写进去的片段」是不是乱码：机械修复把可读引文换成 OCR 垃圾
                    # 毫无意义（实测 13 处里多数如此），正确处置是重新生成该块/重跑该章 OCR
                    if weak_ocr_text(cand):
                        garbled.append((b["id"], aid, cand))
                    else:
                        bad_quotes.append((b["id"], aid, q, cand))
    return {"doc_id": doc.doc_id, "anchors": len(anchors), "stale_ids": stale_ids,
            "missing": missing, "bad_quotes": bad_quotes, "garbled": garbled}


def fix_quotes(doc: DocStore, issues: list[tuple[str, str, str, str]], *, apply: bool,
               log=print) -> tuple[int, int]:
    """把不符的引文替换为锚点内容中的真实片段（CAS + 历史，author=human 之外用 agent）。"""
    fixed = skipped = 0
    for block_id, anchor_id, _old, new in issues:
        if not new:
            skipped += 1
            continue
        block = doc.get_block(block_id, include_deleted=False)
        sources = []
        touched = False
        for s in block.get("sources") or []:
            item = dict(s)
            if item.get("anchor_id") == anchor_id and not touched:
                item["quote"] = new
                touched = True
            sources.append(item)
        if not touched:
            skipped += 1
            continue
        log(f"  {'修复' if apply else '将修复'} {block_id} @ {anchor_id}: {new[:50]!r}")
        if not apply:
            fixed += 1
            continue
        try:
            doc.update_block(block_id, {"sources": sources},
                             expected_revision=block.get("revision"),
                             author="agent", message="修复与锚点不符的引文")
            fixed += 1
        except Exception as exc:      # 人工块上锁等：跳过并如实报告
            log(f"    ⚠️ 跳过 {block_id}: {exc}")
            skipped += 1
    return fixed, skipped


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="数据完整性体检（默认只读）")
    ap.add_argument("--doc", help="只查指定 doc_id")
    ap.add_argument("--fix-quotes", action="store_true", help="预览引文修复")
    ap.add_argument("--apply", action="store_true", help="真正写入（配合 --fix-quotes）")
    args = ap.parse_args()

    layout = Layout(ROOT)
    docs = ([args.doc] if args.doc
            else sorted(p.parent.name for p in layout.data_dir.glob("*/manifest.json")))
    if not docs:
        print("没有找到任何教材喵")
        return 0
    total_bad = total_missing = 0
    for doc_id in docs:
        doc = DocStore(layout, doc_id)
        st = scan_doc(doc)
        total_bad += len(st["bad_quotes"])
        total_missing += len(st["missing"])
        print(f"{doc_id}  锚点 {st['anchors']:>5}  历史遗留 ID {st['stale_ids']:>5}"
              f"  缺引用 {len(st['missing']):>3}  引文不符 {len(st['bad_quotes']):>3}"
              f"  源文本乱码 {len(st['garbled']):>3}")
        for b, aid in st["missing"][:3]:
            print(f"    ❌ 来源指向不存在的锚点：{b} -> {aid}")
        for b, aid, cand in st["garbled"][:3]:
            print(f"    ⚠️ {b} 的锚点原文本身是乱码，修引文没意义——建议重新生成该块/该章"
                  f"（片段：{cand[:40]!r}）")
        if st["bad_quotes"] and args.fix_quotes:
            fixed, skipped = fix_quotes(doc, st["bad_quotes"], apply=args.apply)
            print(f"    {'已修复' if args.apply else '可修复'} {fixed} 处，跳过 {skipped} 处")
    print()
    print(f"合计：引文不符 {total_bad} 处，来源缺失 {total_missing} 处")
    if total_bad and not args.fix_quotes:
        print("提示：加 --fix-quotes 预览修复，再加 --apply 写入（走 CAS + 历史，可回滚）喵")
    if args.apply:
        for doc_id in docs:
            reindex_doc(layout, doc_id)      # 修复后同步索引
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
