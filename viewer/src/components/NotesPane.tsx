import BlockList from "./BlockList";
import BlockEditor from "./BlockEditor";
import AgentCommandBar from "./AgentCommandBar";
import { useStore } from "../state/store";

export default function NotesPane() {
  const blocks = useStore((s) => s.blocks);
  const view = useStore((s) => s.view);
  const setView = useStore((s) => s.setView);
  const selectedBlockId = useStore((s) => s.selectedBlockId);
  const selectBlock = useStore((s) => s.selectBlock);

  return (
    <section className="notes-pane">
      <div className="notes-toolbar">
        <button onClick={() => setView("card")} className={view === "card" ? "active" : ""}>卡片</button>
        <button onClick={() => setView("source")} className={view === "source" ? "active" : ""}>源码</button>
        <button onClick={() => setView("preview")} className={view === "preview" ? "active" : ""}>预览</button>
      </div>
      <BlockList
        blocks={blocks}
        selectedId={selectedBlockId}
        onSelect={selectBlock}
        renderEditor={(b) => (view === "card" ? null : <BlockEditor block={b} mode={view} />)}
      />
      <AgentCommandBar />
    </section>
  );
}
