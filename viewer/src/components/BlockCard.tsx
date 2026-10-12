import { useEffect } from "react";
import { api } from "../api/client";
import { RELATION_CN, STATUS_CN, type NoteBlock } from "../api/types";
import { previewHtml } from "../lib/markdown";
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
  const fetchAnchors = useStore((s) => s.fetchAnchors);
  const docId = useStore((s) => s.docId);
  // 订阅粒度：只取本卡来源的页码字符串（原始值比较），避免任意锚点更新导致全部卡片重渲染
  const sourceKey = useStore((s) =>
    block.sources.map((src) => (s.anchors[src.anchor_id]?.page ?? "?")).join("|"));
  const pages = sourceKey.split("|");
  const setHighlight = useStore((s) => s.setHighlight);
  const openZoom = useStore((s) => s.openZoom);
  const setPdfPage = useStore((s) => s.setPdfPage);
  const setToast = useStore((s) => s.setToast);

  useEffect(() => {
    void fetchAnchors(block.sources.map((s) => s.anchor_id));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [block.sources]);

  const gotoAnchor = async (anchorId: string) => {
    try {
      // 点击时按需读取（useStore.getState 不产生订阅），避免为取页码而重渲染全部卡片
      const cached = useStore.getState().anchors[anchorId];
      const anchor = cached ?? (await api.getAnchor(anchorId));
      setHighlight(anchor);           // 高亮 + 2 秒脉冲 + 滚动居中（6.2）
      setPdfPage(anchor.page);
    } catch (e: any) {
      setToast("锚点跳转失败：" + e.message);
    }
  };

  return (
    <div
      className={`block-card ${selected ? "selected" : ""}`}
      onClick={() => {
        onSelect(block.id);
        openZoom(block.id);   // 点击即放大阅读（小卡片太碎，见 NoteZoom）
      }}
    >
      <div className="block-head">
        <span className="block-type">{TYPE_ICON[block.type] ?? "📝"}</span>
        <span className="block-title">{block.title}</span>
        <span className={`badge status-${block.status}`}>{STATUS_CN[block.status] ?? block.status}</span>
        {block.generation_protected && <span className="badge protected">已保护</span>}
        {block.basis === "synthesis" && <span className="badge synthesis">AI 归纳</span>}
      </div>
      {/* 卡片正文走与「预览」同一渲染器（markdown + KaTeX），不再显示裸文本 */}
      <div
        className="block-preview"
        dangerouslySetInnerHTML={{ __html: previewHtml(block.content || "", docId ?? "") }}
      />
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
            {"📖 p." + (pages[i] ?? "?") + " · " + (RELATION_CN[s.relation] ?? s.relation)}
          </button>
        ))}
      </div>
    </div>
  );
}
