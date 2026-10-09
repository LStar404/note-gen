import BlockCard from "./BlockCard";
import BlockEditor from "./BlockEditor";
import AgentCommandBar from "./AgentCommandBar";
import { useStore } from "../state/store";

export default function NotesPane() {
  const blocks = useStore((s) => s.blocks);
  const view = useStore((s) => s.view);
  const setView = useStore((s) => s.setView);
  const selectedBlockId = useStore((s) => s.selectedBlockId);

  return (
    <section className="notes-pane">
      <div className="notes-toolbar">
        <button onClick={() => setView("card")} className={view === "card" ? "active" : ""}>
          卡片
        </button>
        <button onClick={() => setView("source")} className={view === "source" ? "active" : ""}>
          源码
        </button>
        <button onClick={() => setView("preview")} className={view === "preview" ? "active" : ""}>
          预览
        </button>
      </div>
      <div className="block-list">
        {blocks.length === 0 && <div className="empty">本章还没有笔记块，点击“生成本章”开始。</div>}
        {blocks.map((b) => (
          <div key={b.id} id={`block-${b.id}`}>
            <BlockCard block={b} />
            {selectedBlockId === b.id && view !== "card" && <BlockEditor block={b} mode={view} />}
          </div>
        ))}
      </div>
      <AgentCommandBar />
    </section>
  );
}
