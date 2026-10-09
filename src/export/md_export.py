"""Markdown 导出（9.6）：章级 md、整书 _book.md、Obsidian 兼容变体。"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from jinja2 import Template

from src.core.paths import Layout
from src.core.store import DocStore
from src.export.render import (figure_index, heading_line, live_blocks, rewrite_citations,
                               rewrite_figures, source_badges)

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


def _registry_path(out_dir: Path) -> Path:
    return out_dir / ".export_registry.json"


def _safe_write(out_dir: Path, rel: str, content: str) -> Path:
    """导出通用规则 1：仅覆盖登记 hash 一致的文件；人工改动生成副本并提示。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / rel
    registry_path = _registry_path(out_dir)
    registry = json.loads(registry_path.read_text(encoding="utf-8")) if registry_path.exists() else {}
    new_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    if path.exists():
        cur_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        if registry.get(rel) and registry[rel] != cur_hash:
            path = out_dir / (Path(rel).stem + ".exported" + Path(rel).suffix)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")
    registry[str(rel)] = new_hash
    registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _block_lines(store: DocStore, blocks: list[dict], anchors_by_id: dict[str, dict],
                 figure_paths: dict[str, str], figure_captions: dict[str, str],
                 refs_by_num: dict[int, dict], *, mode: str = "md") -> list[str]:
    lines: list[str] = []
    prev_heading = object()
    for block in blocks:
        anchor = anchors_by_id.get(block.get("heading_anchor_id") or "")
        if block.get("heading_anchor_id") != prev_heading:
            h = heading_line(anchor)
            if h:
                if lines and lines[-1] != "":
                    lines.append("")
                lines.append(h)
                lines.append("")
            prev_heading = block.get("heading_anchor_id")
        icon = {"definition": "📌", "theorem": "📘", "motivation": "💡",
                "figure": "🖼", "link": "🔗"}.get(block.get("type"), "📝")
        badges = source_badges(block, anchors_by_id, mode=mode)
        lines.append(f"## {icon} {block.get('title', '')} {badges}".rstrip())
        lines.append("")
        content = rewrite_figures(block.get("content", ""), doc_id=store.doc_id,
                                  mode="export", figure_paths=figure_paths,
                                  figure_captions=figure_captions)
        content = rewrite_citations(content, refs_by_num, mode="md")
        lines.append(content.strip())
        lines.append("")
        for ln in block.get("links", []):
            if ln.startswith("http://") or ln.startswith("https://"):
                lines.append(f"相关：{ln}")
    return lines


def export_md_chapter(store: DocStore, chapter_id: str, *, out_dir: Path) -> Path:
    manifest = store.manifest()
    chapter = store.chapter(chapter_id)
    anchors_by_id = {a["anchor_id"]: a for a in store.all_anchor_rows()}
    figure_paths, figure_captions = figure_index(store.doc_id, store)
    refs_by_num = {int(r["num"]): r for r in store.ref_rows()}
    blocks = live_blocks(store.block_rows(chapter_id))

    tmpl = (TEMPLATE_DIR / "md_frontmatter.j2").read_text(encoding="utf-8")
    head = Template(tmpl).render(title=chapter["title"], chapter=chapter_id, doc=store.doc_id,
                                 generated_at=manifest.get("updated_at"),
                                 block_count=len(blocks))
    body = "\n".join(_block_lines(store, blocks, anchors_by_id, figure_paths,
                                   figure_captions, refs_by_num))
    return _safe_write(out_dir, f"{chapter_id}.md", head + "\n" + body + "\n")


def export_md_book(store: DocStore, *, out_dir: Path) -> Path:
    """整书导出 = 各章合并 + 目录页（由各章 h1 heading 锚点生成）+ 跨章 links 统一解析。"""
    manifest = store.manifest()
    anchors_by_id = {a["anchor_id"]: a for a in store.all_anchor_rows()}
    parts: list[str] = [f"# {manifest.get('title', store.doc_id)}", ""]
    toc = ["## 目录", ""]
    for ch in manifest.get("chapters", []):
        toc.append(f"- [{ch['title']}](#{ch['chapter_id']})")
    parts.extend(toc)
    parts.append("")
    for ch in manifest.get("chapters", []):
        parts.append(f'<a id="{ch["chapter_id"]}"></a>')
        parts.append("")
        parts.append(f"# {ch['title']}")
        parts.append("")
        figure_paths, figure_captions = figure_index(store.doc_id, store)
        refs_by_num = {int(r["num"]): r for r in store.ref_rows()}
        blocks = live_blocks(store.block_rows(ch["chapter_id"]))
        parts.extend(_block_lines(store, blocks, anchors_by_id, figure_paths,
                                  figure_captions, refs_by_num))
    return _safe_write(out_dir, "_book.md", "\n".join(parts) + "\n")


def export_md_obsidian(store: DocStore, chapter_id: str | None, *, out_dir: Path) -> Path:
    """Obsidian 兼容导出（9.6 规则 5）：块级独立文件 + wikilink + assets 复制。"""
    manifest = store.manifest()
    root = out_dir / store.doc_id
    anchors_by_id = {a["anchor_id"]: a for a in store.all_anchor_rows()}
    figure_paths, figure_captions = figure_index(store.doc_id, store)
    refs_by_num = {int(r["num"]): r for r in store.ref_rows()}
    chapters = [chapter_id] if chapter_id else [c["chapter_id"] for c in manifest.get("chapters", [])]

    (root / "blocks").mkdir(parents=True, exist_ok=True)
    assets = root / "assets"
    for ch in chapters:
        blocks = live_blocks(store.block_rows(ch))
        index_lines = [f"# {store.chapter(ch)['title']}", ""]
        for block in blocks:
            bid = block["id"]
            badges = source_badges(block, anchors_by_id, mode="obsidian")
            tags = " ".join(f"#{t}" for t in block.get("tags", []))
            front = ("---\n"
                     f"title: {json.dumps(block.get('title', ''), ensure_ascii=False)}\n"
                     f"tags: [{', '.join(block.get('tags', []))}]\n"
                     f"source: {json.dumps(manifest.get('title', ''), ensure_ascii=False)}\n"
                     f"block_id: {bid}\n---\n\n")
            content = rewrite_figures(block.get("content", ""), doc_id=store.doc_id,
                                      mode="export",
                                      figure_paths={k: f"assets/{Path(v).name}" for k, v in figure_paths.items()},
                                      figure_captions=figure_captions)
            content = rewrite_citations(content, refs_by_num, mode="md")
            links = []
            for ln in block.get("links", []):
                if ln.startswith("b_"):
                    target = store.blocks_by_id(include_deleted=True).get(ln)
                    if target and not target.get("deleted_at"):
                        links.append(f"[[blocks/{ln}|{target.get('title', ln)}]]")
            body = front + f"## {block.get('title', '')} {badges}\n\n" + content.strip() + "\n"
            if links:
                body += "\n相关： " + "；".join(links) + "\n"
            _safe_write(root, f"blocks/{bid}.md", body)
            index_lines.append(f"- [[blocks/{bid}|{block.get('title', bid)}]] {tags}".rstrip())
        _safe_write(root, f"{ch}.md", "\n".join(index_lines) + "\n")

    # 图片复制到导出子目录 assets
    for fig_id, path in figure_paths.items():
        src = Path(store.layout.root) / path if not Path(path).is_absolute() else Path(path)
        if src.exists():
            assets.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, assets / src.name)
    return root
