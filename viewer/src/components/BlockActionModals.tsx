import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useStore } from "../state/store";

const PRESETS: [string, string][] = [
  ["更精简", "精简内容，保留核心结论与关键条件，删除冗余"],
  ["更详细", "补充细节与解释让内容更易懂，但不得编造笔记之外的事实"],
  ["更准确", "修正不准确的表述与记号，保持原意不变"],
  ["更通俗", "用更直白的语言重写，适合初学者理解"],
];

/** 单块 AI 编辑 + 知识点关联（每块独立入口，便于逐块修改与互链）。 */
export default function BlockActionModals() {
  const aiId = useStore((s) => s.aiEditBlockId);
  const linkId = useStore((s) => s.linkBlockId);
  const closeAiEdit = useStore((s) => s.closeAiEdit);
  const closeLinks = useStore((s) => s.closeLinks);
  const blocks = useStore((s) => s.blocks);
  const docId = useStore((s) => s.docId);
  const setToast = useStore((s) => s.setToast);
  const typeInfo = useStore((s) => s.typeInfo);
  const loadBlocks = useStore((s) => s.loadBlocks);
  const openZoom = useStore((s) => s.openZoom);

  const [instruction, setInstruction] = useState("");
  const [busy, setBusy] = useState(false);
  const [proposal, setProposal] = useState("");
  const [original, setOriginal] = useState("");
  const [search, setSearch] = useState("");

  const activeId = aiId ?? linkId;
  const block = blocks.find((b) => b.id === activeId);

  useEffect(() => {
    setInstruction("");
    setProposal("");
    setOriginal("");
    setSearch("");
  }, [activeId]);

  const runAi = async () => {
    if (!docId || !aiId) return;
    if (!instruction.trim()) {
      setToast("先写修改指令，或点一个快捷指令");
      return;
    }
    setBusy(true);
    try {
      const r = await api.blockAiEdit(docId, aiId, instruction.trim());
      setProposal(r.proposal);
      setOriginal(r.original);
    } catch (e: any) {
      setToast("AI 编辑失败：" + e.message);
    } finally {
      setBusy(false);
    }
  };

  const applyEdit = async () => {
    if (!docId || !aiId || !proposal.trim()) return;
    setBusy(true);
    try {
      await api.blockApplyEdit(docId, aiId, {
        content: proposal,
        base_revision: block?.revision,
      });
      await loadBlocks();
      setToast("已应用修改（原内容保存在历史里，可回溯）");
      closeAiEdit();
    } catch (e: any) {
      setToast("应用失败：" + e.message);
    } finally {
      setBusy(false);
    }
  };

  const addLink = async (targetId: string) => {
    if (!docId || !linkId) return;
    try {
      await api.blockAddLink(docId, linkId, targetId, block?.revision);
      await loadBlocks();
      setToast("已关联知识点");
    } catch (e: any) {
      setToast("关联失败：" + e.message);
    }
  };

  const removeLink = async (targetId: string) => {
    if (!docId || !linkId) return;
    try {
      await api.blockRemoveLink(docId, linkId, targetId, block?.revision);
      await loadBlocks();
    } catch (e: any) {
      setToast("取消关联失败：" + e.message);
    }
  };

  const others = blocks.filter(
    (b) => b.id !== linkId && !b.deleted_at
      && ((b.title ?? "") + (b.content ?? "")).toLowerCase().includes(search.toLowerCase()),
  );

  return (
    <>
      {aiId && block && (
        <div className="modal-mask" onClick={closeAiEdit}>
          <div className="modal wide" onClick={(e) => e.stopPropagation()}>
            <h2>AI 编辑 · {block.title}</h2>
            <div className="muted">修改建议只在你点「应用」后才写入（原内容进历史可回溯）。</div>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", margin: "8px 0" }}>
              {PRESETS.map(([label, hint]) => (
                <button key={label} onClick={() => setInstruction(hint)}>{label}</button>
              ))}
            </div>
            <textarea
              rows={3}
              placeholder="修改指令，例如：把结论提前并补一句适用条件"
              value={instruction}
              onChange={(e) => setInstruction(e.target.value)}
              style={{ width: "100%" }}
            />
            <div className="export-actions">
              <button disabled={busy} onClick={() => void runAi()}>
                {busy ? "生成中…" : "生成修改建议"}
              </button>
            </div>
            {proposal && (
              <>
                <h3>修改建议（可再手改）</h3>
                <textarea
                  rows={10}
                  value={proposal}
                  onChange={(e) => setProposal(e.target.value)}
                  style={{ width: "100%" }}
                />
                {original && (
                  <details style={{ margin: "6px 0" }}>
                    <summary className="muted">对比原文</summary>
                    <pre className="export-path">{original}</pre>
                  </details>
                )}
                <div className="export-actions">
                  <button onClick={() => setProposal("")}>放弃建议</button>
                  <button className="primary" disabled={busy} onClick={() => void applyEdit()}>
                    应用修改
                  </button>
                </div>
              </>
            )}
            {!proposal && (
              <div className="export-actions">
                <button onClick={closeAiEdit}>关闭</button>
              </div>
            )}
          </div>
        </div>
      )}
      {linkId && block && (
        <div className="modal-mask" onClick={closeLinks}>
          <div className="modal wide" onClick={(e) => e.stopPropagation()}>
            <h2>关联知识点 · {block.title}</h2>
            <div className="muted">把相关知识点互链起来（跨章可链），点关联即加入本块的引用。</div>
            {(block.links ?? []).length > 0 && (
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap", margin: "8px 0" }}>
                {(block.links ?? []).map((ln) => {
                  const t = blocks.find((b) => b.id === ln);
                  return (
                    <span key={ln} className="badge">
                      {t ? t.title : ln}
                      <button style={{ marginLeft: 4 }} onClick={() => void removeLink(ln)}>✕</button>
                    </span>
                  );
                })}
              </div>
            )}
            <input
              placeholder="搜索其他知识点…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              style={{ width: "100%", margin: "6px 0" }}
            />
            <div className="review-list" style={{ maxHeight: "40vh", overflowY: "auto" }}>
              {others.slice(0, 60).map((b) => (
                <div key={b.id} className="review-row">
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <b>{typeInfo[b.type]?.icon ?? "📝"} {b.title}</b>
                    <div className="muted" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {(b.content ?? "").slice(0, 90)}
                    </div>
                  </div>
                  {(block.links ?? []).includes(b.id) ? (
                    <button onClick={() => void removeLink(b.id)}>已关联 · 取消</button>
                  ) : (
                    <button onClick={() => void addLink(b.id)}>+ 关联</button>
                  )}
                </div>
              ))}
              {!others.length && <div className="muted">没有匹配的块</div>}
            </div>
            <div className="export-actions">
              <button onClick={() => { openZoom(linkId); closeLinks(); }}>查看本块</button>
              <button className="primary" onClick={closeLinks}>完成</button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
