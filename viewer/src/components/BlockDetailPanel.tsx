import { useEffect, useState } from "react";
import { api, type NoteBlock } from "../api/client";
import { STATUS_CN } from "./BlockCard";
import { useStore } from "../state/store";

export default function BlockDetailPanel() {
  const docId = useStore((s) => s.docId);
  const chapterId = useStore((s) => s.chapterId);
  const blocks = useStore((s) => s.blocks);
  const selectedBlockId = useStore((s) => s.selectedBlockId);
  const selectionHits = useStore((s) => s.selectionHits);
  const loadBlocks = useStore((s) => s.loadBlocks);
  const setPdfPage = useStore((s) => s.setPdfPage);
  const setHighlight = useStore((s) => s.setHighlight);
  const setToast = useStore((s) => s.setToast);
  const [detail, setDetail] = useState<{ block: NoteBlock; history: any[] } | null>(null);

  useEffect(() => {
    if (!selectedBlockId) return setDetail(null);
    api.getBlock(selectedBlockId).then(setDetail).catch(() => setDetail(null));
  }, [selectedBlockId, blocks]);

  const act = async (fn: () => Promise<any>, msg: string) => {
    try {
      await fn();
      setToast(msg);
      await loadBlocks();
    } catch (e: any) {
      setToast("操作失败：" + e.message);
    }
  };

  const jump = async (anchorId: string) => {
    const anchor = await api.getAnchor(anchorId);
    setHighlight(anchor);
    setPdfPage(anchor.page);
  };

  return (
    <aside className="detail-panel">
      <h3>选区命中</h3>
      {selectionHits.length === 0 ? (
        <div className="empty">在 PDF 上选中文字可匹配关联块</div>
      ) : (
        selectionHits.map((h) => (
          <div key={h.anchor_id} className="hit-card" onClick={() => void jump(h.anchor_id)}>
            <div>
              p.{h.page} · {h.match === "geometry" ? "几何命中" : "文本命中"}
            </div>
            <div className="muted">{h.content?.slice(0, 60)}</div>
            <div>
              {(h.blocks ?? []).map((b: any) => (
                <button key={b.block_id} onClick={() => useStore.getState().selectBlock(b.block_id)}>
                  {b.title}
                </button>
              ))}
            </div>
          </div>
        ))
      )}

      <h3>块详情</h3>
      {!detail ? (
        <div className="empty">未选中块</div>
      ) : (
        <div className="detail-body">
          <div>
            <b>{detail.block.title}</b> <span className="muted">{STATUS_CN[detail.block.status]}</span>
          </div>
          <div className="muted">
            revision {detail.block.revision} · {detail.block.type} · order {detail.block.order}
          </div>

          <h4>来源</h4>
          {detail.block.sources.map((s, i) => (
            <div key={i} className="source-row">
              <button onClick={() => void jump(s.anchor_id)}>📖 p跳转</button>
              <span className="muted">{s.quote}</span>
            </div>
          ))}

          <h4>链接</h4>
          {detail.block.links.length === 0 && <div className="muted">无</div>}
          {detail.block.links.map((l) =>
            l.startsWith("b_") ? (
              <button key={l} onClick={() => useStore.getState().selectBlock(l)}>
                {blocks.find((b) => b.id === l)?.title ?? l}
              </button>
            ) : (
              <div key={l} className="muted">
                {l}
              </div>
            )
          )}

          <h4>历史</h4>
          <div className="history">
            {detail.history.map((h) => (
              <div key={h.rev_id} className="history-row">
                <code>{h.rev_id}</code>
                <span className="muted">
                  {h.author} · {h.message} · {h.created_at}
                </span>
                <button
                  onClick={() =>
                    void act(
                      () =>
                        api.rollbackBlock(detail.block.id, {
                          rev_id: h.rev_id,
                          expected_revision: detail.block.revision,
                        }),
                      "已回滚到 " + h.rev_id
                    )
                  }
                >
                  回滚
                </button>
              </div>
            ))}
          </div>

          <div className="detail-actions">
            {detail.block.deleted_at ? (
              <button
                onClick={() =>
                  void act(
                    () => api.restoreBlock(detail!.block.id, { expected_revision: detail!.block.revision }),
                    "已恢复"
                  )
                }
              >
                恢复
              </button>
            ) : (
              <button
                onClick={() =>
                  void act(
                    () => api.deleteBlock(detail!.block.id, { expected_revision: detail!.block.revision }),
                    "已删除（软删除）"
                  )
                }
              >
                删除
              </button>
            )}
            {detail.block.generation_protected && (
              <button
                onClick={() =>
                  void act(
                    () =>
                      api.allowRegeneration(detail!.block.id, {
                        expected_revision: detail!.block.revision,
                        confirmed: true,
                      }),
                    "已允许重新生成覆盖"
                  )
                }
              >
                允许重新生成
              </button>
            )}
            <button
              onClick={() => {
                const after = blocks.filter((b) => b.order < detail!.block.order).pop();
                void act(
                  () =>
                    api.reorder(docId!, chapterId!, {
                      block_id: detail!.block.id,
                      after: after?.id ?? null,
                      expected_revision: detail!.block.revision,
                    }),
                  "已上移"
                );
              }}
            >
              上移
            </button>
            <button
              onClick={() => {
                const next = blocks.find((b) => b.order > detail!.block.order);
                void act(
                  () =>
                    api.reorder(docId!, chapterId!, {
                      block_id: detail!.block.id,
                      after: next?.id ?? detail!.block.id,
                      expected_revision: detail!.block.revision,
                    }),
                  "已下移"
                );
              }}
            >
              下移
            </button>
          </div>
        </div>
      )}
    </aside>
  );
}
