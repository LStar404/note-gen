import { useStore } from "../state/store";
import { api } from "../api/client";
import { STATUS_CN } from "./BlockCard";

export default function ReviewQueue() {
  const open = useStore((s) => s.reviewOpen);
  const items = useStore((s) => s.reviewItems);
  const setOpen = useStore((s) => s.openReview);
  const loadBlocks = useStore((s) => s.loadBlocks);
  const setToast = useStore((s) => s.setToast);
  if (!open) return null;

  const confirm = async (id: string, revision: number) => {
    try {
      await api.updateBlock(id, {
        status: "manually_edited",
        expected_revision: revision,
        message: "人工确认",
        author: "human",
      });
      setToast("已确认");
      await loadBlocks();
      await setOpen(true);
    } catch (e: any) {
      setToast("确认失败：" + e.message);
    }
  };

  return (
    <div className="modal-mask" onClick={() => setOpen(false)}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h2>待审核（needs_review）</h2>
        {items.length === 0 && <div className="empty">没有待审核的块</div>}
        {items.map((b) => (
          <div key={b.id} className="review-row">
            <div>
              <b>{b.title}</b> <span className="muted">{STATUS_CN[b.status]}</span>
            </div>
            <div className="muted">{(b.content ?? "").slice(0, 120)}</div>
            <button onClick={() => useStore.getState().selectBlock(b.id)}>定位</button>
            <button onClick={() => void confirm(b.id, b.revision)}>确认已修</button>
          </div>
        ))}
        <button onClick={() => setOpen(false)}>关闭</button>
      </div>
    </div>
  );
}
