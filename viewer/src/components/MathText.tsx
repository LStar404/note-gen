import { renderInline } from "../lib/markdown";

/** 标题等单行文本的行内渲染：$…$ 公式走 KaTeX（标题曾把原始 LaTeX 直接显示出来）。 */
export default function MathText({ text, docId, className }: {
  text: string;
  docId?: string;
  className?: string;
}) {
  return (
    <span
      className={className}
      dangerouslySetInnerHTML={{ __html: renderInline(text || "", docId ?? "") }}
    />
  );
}