import { useStore } from "../state/store";
import { api } from "../api/client";
import { STATUS_CN } from "./BlockCard";
import MathText from "./MathText";

async function locateBlock(block: any) {
  const st = useStore.getState();
  st.selectBlock(block.id);
  document.getElementById("block-" + block.id)?.scrollIntoView({ behavior: "smooth", block: "center" });
  // 对照 sources 原文（7.4 待审核工作流）
  const first = block.sources?.[0]?.anchor_id;
  if (first) {
    try {
      const anchor = await api.getAnchor(first);
      st.setHighlight(anchor);
      st.setPdfPage(anchor.page);
    } catch {
      /* 锚点缺失时仅定位块 */
    }
  }
}

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
              <b><MathText text={b.title} /></b> <span className="muted">{STATUS_CN[b.status]}</span>
            </div>
            <div className="muted">{(b.content ?? "").slice(0, 120)}</div>
            <div className="muted" style={{ fontSize: 11 }}>
              记录时间 {(b as any).updated_at
                ? String((b as any).updated_at).replace("T", " ").slice(0, 19)
                : "—"}
              （余额/网络类失败是当时的记录：充值或稍后点「继续生成」会自动补全本块）
            </div>
            <button onClick={() => void locateBlock(b)}>定位并对照原文</button>
            <button onClick={() => void confirm(b.id, b.revision)}>确认已修</button>
          </div>
        ))}
        <button onClick={() => setOpen(false)}>关闭</button>
      </div>
    </div>
  );
}
