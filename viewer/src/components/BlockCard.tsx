import { useEffect } from "react";
import { api } from "../api/client";
import { RELATION_CN, STATUS_CN, type NoteBlock } from "../api/types";
import { useStore } from "../state/store";

export { STATUS_CN };

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

export default function BlockCard({ block, selected, onSelect }: {
  block: NoteBlock;
  selected: boolean;
  onSelect: (id: string) => void;
}) {
  const anchors = useStore((s) => s.anchors);
  const fetchAnchors = useStore((s) => s.fetchAnchors);
  const setHighlight = useStore((s) => s.setHighlight);
  const setPdfPage = useStore((s) => s.setPdfPage);
  const setToast = useStore((s) => s.setToast);

  useEffect(() => {
    void fetchAnchors(block.sources.map((s) => s.anchor_id));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [block.sources]);

  const gotoAnchor = async (anchorId: string) => {
    try {
      const anchor = anchors[anchorId] ?? (await api.getAnchor(anchorId));
      setHighlight(anchor);           // 高亮 + 2 秒脉冲 + 滚动居中（6.2）
      setPdfPage(anchor.page);
    } catch (e: any) {
      setToast("锚点跳转失败：" + e.message);
    }
  };

  return (
    <div className={`block-card ${selected ? "selected" : ""}`} onClick={() => onSelect(block.id)}>
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
            title={s.quote}
            onClick={(e) => {
              e.stopPropagation();
              void gotoAnchor(s.anchor_id);
            }}
          >
            {"📖 p." + (anchors[s.anchor_id]?.page ?? "?") + " · " + (RELATION_CN[s.relation] ?? s.relation)}
          </button>
        ))}
      </div>
    </div>
  );
}
