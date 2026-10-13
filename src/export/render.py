"""导出渲染公共规则（9.6）：徽标、标题分组、占位符重写、引文渲染时重写。"""

from __future__ import annotations

import re
from typing import Iterable

from src.core.block_types import RELATION_CN

# 输出语言标签（generate.output_language；导出与徽标文案）
LANG_LABELS: dict[str, dict] = {
    "zh": {
        "types": {"definition": "定义", "theorem": "定理", "motivation": "动机",
                  "figure": "图示", "link": "延伸链接", "contribution": "贡献",
                  "method": "方法", "experiment": "实验"},
        "relations": RELATION_CN,
        "synthesis": "（AI 归纳）", "toc": "目录", "source": "来源",
        "badge": "📖p.{page} · {rel}",
    },
    "en": {
        "types": {"definition": "Definition", "theorem": "Theorem", "motivation": "Motivation",
                  "figure": "Figure", "link": "Link", "contribution": "Contribution",
                  "method": "Method", "experiment": "Experiment"},
        "relations": {"defines": "defines", "states": "states", "proves": "proves",
                      "motivates": "motivates", "illustrates": "illustrates",
                      "summarizes": "summarizes"},
        "synthesis": " (AI synthesis)", "toc": "Contents", "source": "Source",
        "badge": "📖p.{page} · {rel}",
    },
    "ja": {
        "types": {"definition": "定義", "theorem": "定理", "motivation": "動機",
                  "figure": "図", "link": "リンク", "contribution": "貢献",
                  "method": "方法", "experiment": "実験"},
        "relations": {"defines": "定義", "states": "主張", "proves": "証明",
                      "motivates": "動機", "illustrates": "図示", "summarizes": "要約"},
        "synthesis": "（AI による要約）", "toc": "目次", "source": "出典",
        "badge": "📖p.{page} · {rel}",
    },
}

TYPE_ICONS = {"definition": "📌", "theorem": "📘", "motivation": "💡", "figure": "🖼",
              "link": "🔗", "contribution": "🎯", "method": "🧭", "experiment": "📊"}


def labels_for(lang: str = "zh", *, encoding: str = "utf-8") -> dict:
    """导出标签；非 UTF-8 编码时去图标（GBK/Big5 无法表示 emoji）。"""
    labels = dict(LANG_LABELS.get(lang, LANG_LABELS["zh"]))
    utf8 = (encoding or "utf-8").lower().startswith("utf-8")
    labels["icons"] = TYPE_ICONS if utf8 else {}
    return labels


FIG_PLACEHOLDER = re.compile(r"^!\[(fig_[0-9]{8})\]$")
FIG_ANY = re.compile(r"!\[(fig_[0-9]{8})\]")
CITE_PATTERN = re.compile(r"\[(\d{1,4})\]")
MATH_REGION = re.compile(r"(\$\$.*?\$\$|\$.*?\$)", re.DOTALL)
_FENCE = chr(96) * 3
CODE_FENCE = re.compile(_FENCE + ".*?" + _FENCE, re.DOTALL)


def block_order_key(block: dict) -> tuple:
    """不变量 I9：order 升序、同 order 按 block_id 字典序。"""
    return (float(block.get("order", 0.0)), str(block.get("id", "")))


def live_blocks(blocks: Iterable[dict]) -> list[dict]:
    """过滤软删 + I9 排序；顺带内容净化（存量修复）：旧块里的 \\(..\\)/\\[..\\) 数学
    定界与控制字符会让导出被转义成垃圾（实测 72 处公式被毁、块里混过 \x01）。"""
    from src.core.text import normalize_math_text, sanitize_control_chars

    out: list[dict] = []
    for b in sorted([x for x in blocks if not x.get("deleted_at")], key=block_order_key):
        b = dict(b)
        if isinstance(b.get("content"), str):
            b["content"] = normalize_math_text(sanitize_control_chars(b["content"]))
        if isinstance(b.get("title"), str):
            b["title"] = sanitize_control_chars(b["title"])
        out.append(b)
    return out


def source_badges(block: dict, anchors_by_id: dict[str, dict], *, mode: str = "md",
                  labels: dict | None = None) -> str:
    labels = labels or LANG_LABELS["zh"]
    rels = labels.get("relations", RELATION_CN)
    tpl = labels.get("badge", "📖p.{page} · {rel}")
    parts = []
    for src in block.get("sources", []):
        anchor = anchors_by_id.get(src["anchor_id"], {})
        page = anchor.get("page", "?")
        label = tpl.format(page=page, rel=rels.get(src.get("relation"), src.get("relation", "")))
        if mode == "md":
            parts.append(f"[{label}](note://anchor/{src['anchor_id']}?page={page})")
        else:
            parts.append(f"（{label}）")
    if block.get("basis") == "synthesis":
        parts.append(labels.get("synthesis", "（AI 归纳）"))
    return " ".join(parts)


def heading_line(anchor: dict | None) -> str | None:
    """小节标题由块的 heading_anchor_id 驱动（9.6 规则 2）。"""
    if not anchor:
        return None
    level = int(anchor.get("heading_level") or 1)
    marks = "#" * min(3, max(1, level))
    return f"{marks} {anchor.get('content', '').strip()}"


def rewrite_figures(content: str, *, doc_id: str, mode: str, figure_paths: dict[str, str],
                    figure_captions: dict[str, str] | None = None) -> str:
    """图占位符重写：Viewer 用 API URL，导出用磁盘相对路径（7.4 两条规则不得混用）。

    数学/代码区先遮蔽（与 rewrite_citations 同规则）：代码里演示占位符语法、
    公式里出现占位符文本时不得被改写成真图片链接。
    """
    figure_captions = figure_captions or {}

    def repl(m: re.Match) -> str:
        fig_id = m.group(1)
        caption = figure_captions.get(fig_id, figure_paths.get(fig_id, fig_id))
        if mode == "viewer":
            url = f"/api/v1/docs/{doc_id}/figures/{fig_id}"
            return f"![{caption}]({url})"
        return f"![{caption}]({figure_paths.get(fig_id, fig_id)})"

    protected: list[str] = []

    def stash(m: re.Match) -> str:
        protected.append(m.group(0))
        return chr(0) + str(len(protected) - 1) + chr(0)

    text = CODE_FENCE.sub(stash, content)
    text = MATH_REGION.sub(stash, text)
    text = FIG_ANY.sub(repl, text)
    for i, raw in enumerate(protected):
        text = text.replace(chr(0) + str(i) + chr(0), raw)
    return text


def rewrite_citations(content: str, refs_by_num: dict[int, dict], *, mode: str) -> str:
    """引文标记渲染时重写（D18）：仅非数学区、非代码区。"""
    if not refs_by_num:
        return content
    protected: list[str] = []

    def stash(m: re.Match) -> str:
        protected.append(m.group(0))
        return f"\x00{len(protected) - 1}\x00"

    text = CODE_FENCE.sub(stash, content)
    text = MATH_REGION.sub(stash, text)

    def repl(m: re.Match) -> str:
        num = int(m.group(1))
        ref = refs_by_num.get(num)
        if not ref:
            return m.group(0)
        if mode == "md":
            return f"[{num}](note://ref/{ref['ref_id']})"
        return f"[{num}]({ref.get('url') or '#'})"

    text = CITE_PATTERN.sub(repl, text)
    for i, raw in enumerate(protected):
        text = text.replace(f"\x00{i}\x00", raw)
    return text


def figure_index(doc_id: str, store) -> tuple[dict[str, str], dict[str, str]]:
    paths: dict[str, str] = {}
    captions: dict[str, str] = {}
    for row in store.figure_rows():
        paths[row["figure_id"]] = row.get("path", "")
        if row.get("caption"):
            captions[row["figure_id"]] = row["caption"]
    return paths, captions
