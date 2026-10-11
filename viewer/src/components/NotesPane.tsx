import BlockList from "./BlockList";
import BlockEditor from "./BlockEditor";
import AgentCommandBar from "./AgentCommandBar";
import { sortBlocks } from "../api/types";
import { useStore } from "../state/store";

export default function NotesPane() {
  const blocks = useStore((s) => s.blocks);
  const view = useStore((s) => s.view);
  const setView = useStore((s) => s.setView);
  const selectedBlockId = useStore((s) => s.selectedBlockId);
  const selectBlock = useStore((s) => s.selectBlock);
  const dirtyDraft = useStore((s) => s.dirtyDraft);
  const setDraft = useStore((s) => s.setDraft);

  // 视图切换（7.3）：离开编辑态前护住未保存草稿；无选中块时自动选第一个，
  // 否则点「源码/预览」毫无反应（用户视角就是切换坏了）
  const switchView = (v: "card" | "source" | "preview") => {
    if (v === view) return;
    if (view === "source" && dirtyDraft !== null) {
      const ok = window.confirm("当前块有未保存草稿，切换视图将丢弃草稿，继续吗？");
      if (!ok) return;
      setDraft(null);
    }
    setView(v);
    if (v !== "card" && !selectedBlockId) {
      const first = sortBlocks(blocks.filter((b) => !b.deleted_at))[0];
      if (first) selectBlock(first.id);
    }
  };

  return (
    <section className="notes-pane">
      <div className="notes-toolbar">
        <button onClick={() => switchView("card")} className={view === "card" ? "active" : ""}>卡片</button>
        <button onClick={() => switchView("source")} className={view === "source" ? "active" : ""}>源码</button>
        <button onClick={() => switchView("preview")} className={view === "preview" ? "active" : ""}>预览</button>
      </div>
      <BlockList
        blocks={blocks}
        selectedId={selectedBlockId}
        onSelect={selectBlock}
        renderEditor={(b) =>
          view === "card" ? null : (
            // key 带 revision：生成/保存后数据更新要重挂载刷新内容，
            // 而视图切换（source↔preview）保持草稿不丢
            <BlockEditor key={b.id + ":" + b.revision} block={b} mode={view} />
          )
        }
      />
      <AgentCommandBar />
    </section>
  );
}
