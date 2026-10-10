"""LaTeX 导出（9.6）：受限模板、Markdown/数学 AST 转换、环境映射。"""

from __future__ import annotations

import re
from pathlib import Path

from src.core.block_types import get_type
from src.core.store import DocStore
from src.export.md_export import _safe_write
from src.export.render import (figure_index, heading_line, labels_for, live_blocks,
                               rewrite_citations, source_badges)

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
B = chr(92)   # 反斜杠，避免转义歧义

TEX_SPECIALS = {
    B: B + "textbackslash{}", "{": B + "{", "}": B + "}", "$": B + "$", "&": B + "&",
    "#": B + "#", "^": B + "textasciicircum{}", "_": B + "_", "~": B + "textasciitilde{}",
    "%": B + "%",
}

MATH_RE = re.compile(r"(\$\$.*?\$\$|\$.*?\$)", re.DOTALL)
LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_FENCE = chr(96) * 3
FENCE_RE = re.compile(_FENCE + "(.*?)" + _FENCE, re.DOTALL)


def tex_escape(text: str) -> str:
    return "".join(TEX_SPECIALS.get(ch, ch) for ch in text)


def md_inline_to_tex(text: str) -> str:
    """行内 Markdown → TeX：数学优先保护，再处理图片/链接/代码/强调。"""
    parts: list[tuple[str, str]] = []
    last = 0
    for m in MATH_RE.finditer(text):
        parts.append(("text", text[last:m.start()]))
        seg = m.group(0)
        if seg.startswith("$$"):
            parts.append(("math", B + "begin{equation*}" + seg[2:-2] + B + "end{equation*}"))
        else:
            parts.append(("math", B + "(" + seg[1:-1] + B + ")"))
        last = m.end()
    parts.append(("text", text[last:]))

    out: list[str] = []
    for kind, seg in parts:
        if kind == "math":
            out.append(seg)
            continue
        seg = IMAGE_RE.sub(
            lambda m: (B + "begin{figure}[h]" + B + "centering" + B + "includegraphics[width=0.8" +
                       B + "linewidth]{" + m.group(2) + "}" + B + "caption{" + tex_escape(m.group(1)) +
                       "}" + B + "end{figure}"), seg)
        seg = LINK_RE.sub(lambda m: B + "href{" + m.group(2) + "}{" + tex_escape(m.group(1)) + "}", seg)
        seg = FENCE_RE.sub(lambda m: B + "begin{verbatim}" + m.group(1) + B + "end{verbatim}", seg)
        pieces = re.split(r"(\*\*[^*]+?\*\*|(?<!\*)\*[^*]+?\*(?!\*))", seg)
        buf = ""
        for piece in pieces:
            if len(piece) > 4 and piece.startswith("**") and piece.endswith("**"):
                buf += B + "textbf{" + tex_escape(piece[2:-2]) + "}"
            elif len(piece) > 2 and piece.startswith("*") and piece.endswith("*"):
                buf += B + "textit{" + tex_escape(piece[1:-1]) + "}"
            else:
                buf += tex_escape(piece)
        out.append(buf)
    return "".join(out)


def md_block_to_tex(content: str) -> str:
    """块级 Markdown → TeX：列表/段落 + 行内转换（基于 token 切分，不用单条正则替换）。"""
    out: list[str] = []
    in_item = False
    for line in content.split("\n"):
        stripped = line.strip()
        if not stripped:
            if in_item:
                out.append(B + "end{itemize}")
                in_item = False
            continue
        if stripped.startswith("$$"):
            out.append(stripped)
            continue
        if stripped.startswith("- ") or stripped.startswith("* "):
            if not in_item:
                out.append(B + "begin{itemize}")
                in_item = True
            out.append(B + "item " + md_inline_to_tex(stripped[2:]))
            continue
        if in_item:
            out.append(B + "end{itemize}")
            in_item = False
        m = re.match(r"^(#+)\s+(.*)$", stripped)
        if m:
            level = len(m.group(1))
            cmd = {1: "section", 2: "subsection", 3: "subsubsection"}.get(level, "paragraph")
            out.append(B + cmd + "{" + tex_escape(m.group(2)) + "}")
            continue
        out.append(md_inline_to_tex(stripped))
    if in_item:
        out.append(B + "end{itemize}")
    return "\n".join(out)


def export_tex_chapter(store: DocStore, chapter_id: str, *, out_dir: Path,
                       lang: str = "zh", encoding: str = "utf-8") -> Path:
    labels = labels_for(lang, encoding=encoding)
    manifest = store.manifest()
    chapter = store.chapter(chapter_id)
    anchors_by_id = {a["anchor_id"]: a for a in store.all_anchor_rows()}
    figure_paths, figure_captions = figure_index(store.doc_id, store)
    refs_by_num = {int(r["num"]): r for r in store.ref_rows()}
    blocks = live_blocks(store.block_rows(chapter_id))

    preamble = (TEMPLATE_DIR / "tex_preamble.tex").read_text(encoding="utf-8")
    body: list[str] = ["% " + manifest.get("title", "") + " / " + chapter["title"], ""]
    prev_heading = object()
    for block in blocks:
        anchor = anchors_by_id.get(block.get("heading_anchor_id") or "")
        if block.get("heading_anchor_id") != prev_heading:
            h = heading_line(anchor)
            if h:
                level = h.split(" ")[0].count("#")
                cmd = {1: "section", 2: "subsection"}.get(level, "subsubsection")
                title = h.lstrip("#").strip()
                body.append(B + cmd + "{" + tex_escape(title) + "}")
                prev_heading = block.get("heading_anchor_id")

        spec = get_type(block.get("type", "link"))
        env = spec.tex_env
        title = tex_escape(block.get("title", ""))
        content = rewrite_citations(block.get("content", ""), refs_by_num, mode="tex")
        for fig_id, path in figure_paths.items():
            content = content.replace(f"![{fig_id}]",
                                      f"![{figure_captions.get(fig_id, fig_id)}]({path})")
        rendered = md_block_to_tex(content)
        badges = source_badges(block, anchors_by_id, mode="tex", labels=labels)
        if env:
            body.append(B + "begin{" + env + "}[" + title + "]")
            body.append(rendered)
            body.append(B + "end{" + env + "}")
        else:
            body.append(B + "par" + B + "noindent" + B + "textbf{" + title + "}" + B +
                        " " + tex_escape(badges))
            body.append(rendered)
        body.append(B + "label{blk:" + block["id"] + "}")
        for ln in block.get("links", []):
            if ln.startswith("http://") or ln.startswith("https://"):
                body.append(B + "footnote{" + B + "href{" + ln + "}{" + tex_escape(ln) + "}}")
            elif ln.startswith("b_"):
                body.append(B + "footnote{见块 " + B + "ref{blk:" + ln + "}}")
        body.append("")

    body.append(B + "end{document}")
    return _safe_write(out_dir, f"{chapter_id}.tex", preamble + "\n" + "\n".join(body) + "\n",
                       encoding=encoding)


def export_tex_book(store: DocStore, *, out_dir: Path, lang: str = "zh",
                    encoding: str = "utf-8") -> Path:
    manifest = store.manifest()
    preamble = (TEMPLATE_DIR / "tex_preamble.tex").read_text(encoding="utf-8")
    parts = [B + "tableofcontents", ""]
    tmp_dir = out_dir / "_tex_tmp"
    for ch in manifest.get("chapters", []):
        parts.append(B + "section{" + tex_escape(ch["title"]) + "}")
        path = export_tex_chapter(store, ch["chapter_id"], out_dir=tmp_dir,
                                  lang=lang, encoding=encoding)
        text = path.read_text(encoding="utf-8")
        start = text.index(B + "begin{document}") + len(B + "begin{document}")
        end = text.rindex(B + "end{document}")
        parts.append(text[start:end])
    return _safe_write(out_dir, "_book.tex",
                       preamble + "\n" + "\n".join(parts) + "\n" + B + "end{document}\n",
                       encoding=encoding)
