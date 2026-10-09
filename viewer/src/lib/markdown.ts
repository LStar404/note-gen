/** 前端预览渲染：markdown-it(html:false) + KaTeX + 图占位符重写（7.4）。 */

import MarkdownIt from "markdown-it";
import katex from "katex";

const md = new MarkdownIt({ html: false, linkify: true, breaks: false });

const FENCE = String.fromCharCode(96, 96, 96);
const CODE_FENCE = new RegExp(FENCE + ".*?" + FENCE, "gs");
const MATH = /(\$\$.*?\$\$|\$.*?\$)/gs;
const FIG = /!\[(fig_\d{8})\]/g;

function protectRegions(text: string): { text: string; kept: string[] } {
  const kept: string[] = [];
  const stash = (m: string) => {
    kept.push(m);
    return "\u0000" + (kept.length - 1) + "\u0000";
  };
  let out = text.replace(CODE_FENCE, stash);
  out = out.replace(MATH, stash);
  return { text: out, kept };
}

function restoreRegions(text: string, kept: string[]): string {
  kept.forEach((raw, i) => {
    text = text.replace("\u0000" + i + "\u0000", raw);
  });
  return text;
}

/** 图占位符重写为 API URL；代码块/数学区不改写（与导出渲染规则分离）。 */
export function rewriteFigures(content: string, docId: string, captions: Record<string, string>): string {
  const { text, kept } = protectRegions(content);
  const out = text.replace(FIG, (_m, figId: string) =>
    "![" + (captions[figId] ?? figId) + "](/api/v1/docs/" + docId + "/figures/" + figId + ")"
  );
  return restoreRegions(out, kept);
}

export function renderMarkdown(content: string, docId: string, captions: Record<string, string> = {}): string {
  const rewritten = rewriteFigures(content, docId, captions);
  return md.render(rewritten);
}

export function renderMathOnly(html: string): string {
  return html
    .replace(/\$\$(.+?)\$\$/gs, (_m, tex: string) => {
      try {
        return katex.renderToString(tex, { displayMode: true, throwOnError: false });
      } catch {
        return "<pre>" + tex + "</pre>";
      }
    })
    .replace(/(?<!\$)\$([^$\n]+?)\$(?!\$)/g, (_m, tex: string) => {
      try {
        return katex.renderToString(tex, { displayMode: false, throwOnError: false });
      } catch {
        return "<code>" + tex + "</code>";
      }
    });
}
