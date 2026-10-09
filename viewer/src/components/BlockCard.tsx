import { api, type NoteBlock, type Anchor } from "../api/client";
import { useStore } from "../state/store";

const TYPE_ICON: Record<string, string> = {
  definition: "📌",
  theorem: "📘",
  motivation: "💡",
  figure: "🖼",
  link: "🔗",
  contribution: "🎯",
  method: "🧭",
  experiment: "📊",
};

const RELATION_CN: Record<string, string> = {
  defines: "定义",
  states: "陈述",
  proves: "证明",
  motivates: "动机",
  illustrates: "图示",
  summarizes: "归纳",
};

export const STATUS_CN: Record<string, string> = {
  ai_generated: "AI 生成",
  hybrid: "混合",
  manually_edited: "人工编辑",
  needs_review: "待审核",
};

export default function BlockCard({ block }: { block: NoteBlock }) {
  const selectBlock = useStore((s) => s.selectBlock);
  const selectedBlockId = useStore((s) => s.selectedBlockId);
  const setHighlight = useStore((s) => s.setHighlight);
  const setPdfPage = useStore((s) => s.setPdfPage);
  const setToast = useStore((s) => s.setToast);

  const gotoAnchor = async (anchorId: string) => {
    try {
      const anchor: Anchor = await api.getAnchor(anchorId);
      setHighlight(anchor);
      setPdfPage(anchor.page);
    } catch (e: any) {
      setToast("锚点跳转失败：" + e.message);
    }
  };

  return (
    <div
      className={`block-card ${selectedBlockId === block.id ? "selected" : ""}`}
      onClick={() => selectBlock(block.id)}
    >
      <div className="block-head">
        <span className="block-type">{TYPE_ICON[block.type] ?? "📝"}</span>
        <span className="block-title">{block.title}</span>
        <span className={`badge status-${block.status}`}>{STATUS_CN[block.status] ?? block.status}</span>
        {block.generation_protected && <span className="badge protected">已保护</span>}
        {block.basis === "synthesis" && <span className="badge synthesis">AI 归纳</span>}
      </div>
      <div className="block-preview">{(block.content || "").slice(0, 120)}</div>
      <div className="block-sources">
        {block.sources.map((s, i) => (
          <button
            key={i}
            className="source-badge"
            onClick={(e) => {
              e.stopPropagation();
              void gotoAnchor(s.anchor_id);
            }}
          >
            📖 {RELATION_CN[s.relation] ?? s.relation}
          </button>
        ))}
      </div>
    </div>
  );
}
