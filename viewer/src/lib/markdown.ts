/** 前端预览渲染（规范 7.4 MUST）：
 *  - markdown-it 关闭原始 HTML（html:false）+ KaTeX 默认安全配置；
 *  - 图占位符 ![{fig_id}] 在 inline token 阶段识别为专用 figure token（math 规则先注册，
 *    代码/数学区天然不被改写），渲染为 API URL；
 *  - 数学 $…$ / $$…$$ 走 KaTeX renderer，不对已渲染 HTML 做正则后处理。
 */

import MarkdownIt from "markdown-it";
import katex from "katex";
import "katex/dist/katex.min.css";   // KaTeX 字体/排版样式：缺了公式显示为乱排的 HTML

export interface RenderEnv {
  docId: string;
  captions?: Record<string, string>;
}

const FIG_RE = /^!\[(fig_\d{8})\]/;
const MATH_INLINE_RE = /^\$([^$\n]+?)\$/;

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function renderMath(tex: string, displayMode: boolean): string {
  try {
    return katex.renderToString(tex, {
      displayMode,
      throwOnError: false,
      // 默认安全配置：不信任 \html 等可执行扩展
      trust: false,
      strict: "warn",
    });
  } catch {
    return "<code>" + escapeHtml(tex) + "</code>";
  }
}

/** 数学规则：块级 $$…$$ 优先于行内 $…$。 */
function mathPlugin(md: MarkdownIt): void {
  md.block.ruler.before(
    "fence",
    "math_block",
    (state, startLine, endLine, silent) => {
      const startPos = state.bMarks[startLine] + state.tShift[startLine];
      const maxPos = state.eMarks[startLine];
      const first = state.src.slice(startPos, maxPos).trim();
      if (!first.startsWith("$$")) return false;
      if (silent) return true;

      let content = first.slice(2);
      let lineNo = startLine;
      if (!(content.endsWith("$$") && content.length >= 2)) {
        const buf = [content];
        lineNo = startLine + 1;
        let closed = false;
        while (lineNo < endLine) {
          const s = state.bMarks[lineNo] + state.tShift[lineNo];
          const e = state.eMarks[lineNo];
          const text = state.src.slice(s, e);
          if (text.trim().endsWith("$$")) {
            buf.push(text.trim().slice(0, -2));
            closed = true;
            break;
          }
          buf.push(text);
          lineNo += 1;
        }
        if (!closed) return false;
        content = buf.join("\n");
      } else {
        content = content.slice(0, -2);
      }
      const token = state.push("math_block", "math", 0);
      token.content = content;
      token.map = [startLine, lineNo + 1];
      state.line = lineNo + 1;
      return true;
    },
    { alt: ["paragraph", "reference", "blockquote", "list"] }
  );

  md.inline.ruler.after("escape", "math_inline", (state, silent) => {
    const start = state.pos;
    if (state.src.charCodeAt(start) !== 0x24 /* $ */) return false;
    const match = MATH_INLINE_RE.exec(state.src.slice(start));
    if (!match) return false;
    if (!silent) {
      const token = state.push("math_inline", "math", 0);
      token.content = match[1];
    }
    state.pos += match[0].length;
    return true;
  });

  md.renderer.rules.math_inline = (tokens, idx) => renderMath(tokens[idx].content, false);
  md.renderer.rules.math_block = (tokens, idx) =>
    "<p>" + renderMath(tokens[idx].content, true) + "</p>";
}

/** 图占位符专用 token：仅在 inline 阶段、非数学/代码区命中。 */
function figurePlugin(md: MarkdownIt): void {
  md.inline.ruler.before("image", "figure", (state, silent) => {
    const match = FIG_RE.exec(state.src.slice(state.pos));
    if (!match) return false;
    if (!silent) {
      const token = state.push("figure", "img", 0);
      token.meta = { figId: match[1] };
      token.content = match[1];
    }
    state.pos += match[0].length;
    return true;
  });

  md.renderer.rules.figure = (tokens, idx, _options, env) => {
    const { figId } = tokens[idx].meta as { figId: string };
    const renderEnv = (env ?? {}) as RenderEnv;
    const caption = renderEnv.captions?.[figId] ?? figId;
    const docId = renderEnv.docId ?? "";
    return (
      '<img src="/api/v1/docs/' +
      docId +
      "/figures/" +
      figId +
      '" alt="' +
      escapeHtml(caption) +
      '" onerror="this.style.display=' + "'none'" + '">'
    );
  };
}

function createRenderer(): MarkdownIt {
  const md = new MarkdownIt({ html: false, linkify: true, breaks: false });
  mathPlugin(md);    // 先注册数学：figure 规则天然跳过数学/代码区
  figurePlugin(md);
  return md;
}

const renderer = createRenderer();

export function renderMarkdown(content: string, docId: string, captions: Record<string, string> = {}): string {
  return renderer.render(content || "", { docId, captions } as RenderEnv);
}

/** 行内渲染（标题等单行文本）：$…$ 走 KaTeX，无块级包装。 */
export function renderInline(content: string, docId: string = ""): string {
  return renderer.renderInline(content || "", { docId, captions: {} } as RenderEnv);
}

/** 卡片预览截断：按行边界截，绝不切断公式——
 *  $…$ 成对保留；$$…$$ 块整体保留或整体丢弃（截断后计数为奇数就回退到开标记前）。 */
export function previewSource(content: string, maxChars = 320): string {
  const src = content || "";
  if (src.length <= maxChars) return src;
  let cut = src.lastIndexOf("\n", maxChars);
  if (cut < 40) cut = maxChars;
  let head = src.slice(0, cut);
  const dbl = (head.match(/\$\$/g) || []).length;
  if (dbl % 2 === 1) {
    head = head.slice(0, head.lastIndexOf("$$"));
  }
  const single = (head.replace(/\$\$/g, "").match(/\$/g) || []).length;
  if (single % 2 === 1) {
    head = head.slice(0, head.lastIndexOf("$"));
  }
  return head.trimEnd() + "\n…";
}

/** 卡片预览 HTML：与「预览」视图同一渲染器（markdown + KaTeX）。 */
export function previewHtml(content: string, docId: string): string {
  return renderMarkdown(previewSource(content), docId ?? "", {});
}

/** 图占位符重写（供导出对照逻辑复用）；代码/数学区不改写。 */
export function rewriteFigures(content: string, docId: string, captions: Record<string, string> = {}): string {
  return content.replace(/!\[(fig_\d{8})\]/g, (_m, figId: string) =>
    "![" + (captions[figId] ?? figId) + "](/api/v1/docs/" + docId + "/figures/" + figId + ")"
  );
}
