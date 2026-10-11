"""Anki 闪卡导出（11.4 / E 类学习闭环）：definition/theorem/motivation → 卡片。

正面 = 概念/标题；背面 = content + 来源页码；优先生成 .apkg（genanki），
不可用时回退 CSV（Anki 可导入）。卡片与 deck ID 由 doc/章确定性派生，重复导入即更新。
"""

from __future__ import annotations

import csv
import hashlib
import html
import re
from pathlib import Path

from src.core.store import DocStore

from .render import figure_index, live_blocks, rewrite_citations

CARD_TYPES = ("definition", "theorem", "motivation")
MATH_RE = re.compile(r"(\$\$.*?\$\$|\$.*?\$)", re.DOTALL)


def _stable_id(*parts: str) -> int:
    """genanki 需要 31 位整数 ID；由内容确定性派生，重复导入即更新。"""
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % (2 ** 31)


def _math_to_anki(text: str) -> str:
    r"""Anki/MathJax 使用 \( \) 与 \[ \]；保持内容本身不动（对拍友好）。"""
    def repl(m: re.Match) -> str:
        seg = m.group(0)
        if seg.startswith("$$"):
            return "\\[" + seg[2:-2] + "\\]"
        return "\\(" + seg[1:-1] + "\\)"
    return MATH_RE.sub(repl, text)


def _back_html(block: dict, anchors_by_id: dict[str, dict]) -> str:
    content = _math_to_anki(block.get("content", ""))
    pages = []
    for src in block.get("sources", []):
        page = (anchors_by_id.get(src["anchor_id"]) or {}).get("page")
        if page and page not in pages:
            pages.append(page)
    # 必须 HTML 转义：数学里的 <（R<\infty）会被当标签吞掉，Anki 渲染残缺。
    # 实体在文本节点里被浏览器还原，MathJax 照常读到原始 <。
    content_html = html.escape(content).replace("\n", "<br/>")
    footer = ("来源页码：" + "、".join(f"p.{p}" for p in pages)) if pages else "（AI 归纳，无来源页码）"
    return f"<div>{content_html}</div><hr/><div style='color:#666;font-size:12px'>{footer}</div>"


def collect_cards(store: DocStore, chapter_id: str | None = None) -> list[dict]:
    anchors_by_id = {a["anchor_id"]: a for a in store.all_anchor_rows()}
    figure_paths, figure_captions = figure_index(store.doc_id, store)
    refs_by_num = {int(r["num"]): r for r in store.ref_rows()}
    chapters = [chapter_id] if chapter_id else [c["chapter_id"] for c in store.manifest().get("chapters", [])]
    cards: list[dict] = []
    for ch in chapters:
        for block in live_blocks(store.block_rows(ch)):
            if block.get("type") not in CARD_TYPES:
                continue
            content = rewrite_citations(block.get("content", ""), refs_by_num, mode="md")
            content = rewrite_figures_plain(content, figure_paths, figure_captions)
            cards.append({
                "block_id": block["id"],
                "chapter_id": ch,
                "type": block["type"],
                "front": block.get("title", ""),
                "back": _back_html({**block, "content": content}, anchors_by_id),
                "back_text": block.get("content", ""),
                "tags": [block.get("type", "card")] + list(block.get("tags", [])),
                "pages": sorted({(anchors_by_id.get(s["anchor_id"]) or {}).get("page")
                                 for s in block.get("sources", [])} - {None}),
            })
    return cards


def rewrite_figures_plain(content: str, figure_paths: dict[str, str],
                          figure_captions: dict[str, str]) -> str:
    """卡片内图占位符降级为纯文本图注（Anki 资源另行打包，避免断链）。"""
    import re as _re

    def repl(m: _re.Match) -> str:
        fig_id = m.group(1)
        return f"［图：{figure_captions.get(fig_id, figure_paths.get(fig_id, fig_id))}］"

    return _re.sub(r"!\[(fig_\d{8})\]", repl, content)


def export_anki(store: DocStore, chapter_id: str | None = None, *, out_dir: Path,
                deck_name: str | None = None, lang: str = "zh",
                encoding: str = "utf-8") -> Path:
    cards = collect_cards(store, chapter_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"-{chapter_id}" if chapter_id else ""
    deck_name = deck_name or f"note-gen · {store.manifest().get('title', store.doc_id)}{suffix}"

    try:
        import genanki
    except Exception:
        return _export_csv(store, cards, out_dir, suffix, encoding=encoding)

    model_id = _stable_id("note-gen-model", "v1")
    deck_id = _stable_id("note-gen-deck", store.doc_id, chapter_id or "_book")
    model = genanki.Model(
        model_id,
        "note-gen 笔记卡",
        fields=[{"name": "Front"}, {"name": "Back"}, {"name": "Source"}],
        templates=[{
            "name": "卡片",
            "qfmt": "<div class='q'>{{Front}}</div>",
            "afmt": "{{FrontSide}}<hr id='answer'><div class='a'>{{Back}}</div>",
        }],
        css=".q{font-size:20px;} .a{font-size:16px;}",
    )
    deck = genanki.Deck(deck_id, deck_name)
    for card in cards:
        note = genanki.Note(
            model=model,
            fields=[html.escape(card["front"]), card["back"],
                    "、".join(f"p.{p}" for p in card["pages"]) or "-"],
            tags=[t.replace(" ", "_") for t in card["tags"] if t],
            guid=genanki.guid_for(store.doc_id, card["block_id"]),
        )
        deck.add_note(note)
    out = out_dir / f"{store.doc_id}{suffix}.apkg"
    genanki.Package(deck).write_to_file(str(out))
    return out


def _export_csv(store: DocStore, cards: list[dict], out_dir: Path, suffix: str,
                encoding: str = "utf-8") -> Path:
    """genanki 不可用时的回退：可导入 CSV（front,back,tags）。"""
    out = out_dir / f"{store.doc_id}{suffix}-anki.csv"
    with out.open("w", encoding=encoding, errors="replace", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["front", "back", "tags", "source"])
        for card in cards:
            writer.writerow([card["front"], card["back_text"],
                             ";".join(card["tags"]),
                             "、".join(f"p.{p}" for p in card["pages"])])
    return out
