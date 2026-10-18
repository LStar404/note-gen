"""底层操作基准：在真实教材的临时副本上量热点（不动原始数据）。

用法：python scripts/bench_ops.py [doc_id]
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core.paths import Layout          # noqa: E402
from src.core.store import DocStore        # noqa: E402


def bench(name: str, fn, n: int = 5, repeat_warm: bool = True):
    ts = []
    try:
        if repeat_warm:
            fn()                       # 预热（缓存/惰性导入）
        for _ in range(n):
            t = time.perf_counter()
            out = fn()
            ts.append((time.perf_counter() - t) * 1000)
    except Exception as exc:
        print(f"{name:38s} ERROR {exc}")
        traceback.print_exc()
        return None
    print(f"{name:38s} min {min(ts):8.1f} ms   median {sorted(ts)[len(ts)//2]:8.1f} ms   (n={n})")
    return out


def main() -> int:
    doc_id = sys.argv[1] if len(sys.argv) > 1 else "doc-e8934cbdf65f"
    src_root = ROOT
    src_doc = src_root / "data" / doc_id
    if not src_doc.exists():
        print(f"找不到教材 {doc_id}")
        return 1

    tmp = Path(tempfile.mkdtemp(prefix="ng-bench-"))
    # 复制成独立工作区：data/<doc> + data/textbooks + runtime.db + config
    (tmp / "data").mkdir(parents=True)
    shutil.copytree(src_doc, tmp / "data" / doc_id)
    (tmp / "data" / "textbooks").mkdir(exist_ok=True)
    for p in (src_root / "data" / "textbooks").glob(doc_id + ".*"):
        shutil.copy2(p, tmp / "data" / "textbooks" / p.name)
    for name in ("models.json", ".local_token"):
        if (src_root / "data" / name).exists():
            shutil.copy2(src_root / "data" / name, tmp / "data" / name)
    if (src_root / "config.yaml").exists():
        shutil.copy2(src_root / "config.yaml", tmp / "config.yaml")

    layout = Layout(tmp).ensure_base()
    store = DocStore(layout, doc_id)
    chapters = store.chapter_ids()
    print(f"教材 {doc_id}: {len(chapters)} 章, 工作区 {tmp}")
    print(f"锚点 {len(store.all_anchor_rows())} / 块 {len(store.all_block_rows(include_deleted=True))}")
    print("-" * 78)

    big_ch = max(chapters, key=lambda c: len(store.block_rows(c, include_deleted=True)))
    print(f"最大章 {big_ch}: 块 {len(store.block_rows(big_ch, include_deleted=True))}")
    bench("block_rows(1 章)", lambda: store.block_rows(big_ch, include_deleted=True), n=3)
    bench("anchor_rows(1 章)", lambda: store.anchor_rows(big_ch), n=3)
    bench("anchors_by_id() 全书", lambda: store.anchors_by_id(), n=3)
    bench("blocks_by_id() 全书", lambda: store.blocks_by_id(include_deleted=True), n=3)
    bench("all_anchor_rows()", lambda: store.all_anchor_rows(), n=3)
    bench("compute_backlinks(全书)", lambda: store.compute_backlinks(store.all_block_rows(True)), n=3)

    from src.db.reindex import reindex_doc
    bench("reindex_doc 全量", lambda: reindex_doc(layout, doc_id), n=3)
    bench("reindex_doc blocks_only(全书快路径)", lambda: reindex_doc(layout, doc_id, blocks_only=True), n=3)
    bench(f"reindex_doc blocks_only(仅 {big_ch})",
          lambda: reindex_doc(layout, doc_id, blocks_only=True, chapters=[big_ch]), n=3)

    from src.db.queries import blocks_for_page
    try:
        pages = sorted({b["order"] for b in []}) or []
        b = store.block_rows(big_ch)[0]
        page = b.get("created_at") and None
    except Exception:
        pass
    # 页码来自锚点
    anchor = store.anchor_rows(big_ch)[0]
    page_no = int(anchor["page"])
    bench(f"blocks_for_page(p.{page_no}) [SQLite]",
          lambda: blocks_for_page(layout, doc_id, page_no), n=3)

    # 单块编辑（计一次完整提交：事务目录+JSONL+reindex+历史）
    target = max(store.block_rows(big_ch), key=lambda x: int(x.get("revision", 0)))
    bid = target["id"]
    def edit_block():
        st = DocStore(layout, doc_id)
        blk = st.get_block(bid, include_deleted=False)
        st.update_block(bid, {"content": (blk.get("content") or "") + "喵"},
                        expected_revision=blk.get("revision"), author="human",
                        message="bench", unlocked_for_agent=True)
    bench("update_block 一次完整提交", edit_block, n=3)

    from src.link.backlink import link_doc
    bench("link_doc(单章)", lambda: link_doc(doc_id, big_ch, layout=layout), n=3)

    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
