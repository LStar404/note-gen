import { useState } from "react";
import { api } from "../api/client";
import { useStore } from "../state/store";

export default function AgentCommandBar() {
  const docId = useStore((s) => s.docId);
  const chapterId = useStore((s) => s.chapterId);
  const selectedBlockId = useStore((s) => s.selectedBlockId);
  const agent = useStore((s) => s.agent);
  const setAgent = useStore((s) => s.setAgent);
  const loadBlocks = useStore((s) => s.loadBlocks);
  const setToast = useStore((s) => s.setToast);
  const [instruction, setInstruction] = useState("");

  const url = selectedBlockId
    ? `/api/v1/blocks/${selectedBlockId}/agent-edit`
    : `/api/v1/docs/${docId}/chapters/${chapterId}/agent-edit`;

  const plan = async () => {
    if (!instruction.trim()) return;
    setAgent({ running: true, error: null, diff: null });
    try {
      const res = await api.agentEdit(url, { mode: "plan", instruction });
      setAgent({ plan: res.plan, planId: res.plan_id, running: false });
    } catch (e: any) {
      setAgent({ running: false, error: e.message });
    }
  };

  const exec = async () => {
    if (!agent.planId) return;
    setAgent({ running: true, error: null });
    try {
      const res = await api.agentEdit(url, {
        mode: "exec",
        plan_id: agent.planId,
        confirmed: true,
      });
      setAgent({ running: false, diff: res.diff, error: res.error?.error ?? null });
      setToast(res.partial ? "agent 部分执行完成" : "agent 编辑完成");
      await loadBlocks();
    } catch (e: any) {
      setAgent({ running: false, error: e.message });
    }
  };

  const undo = async () => {
    for (const item of agent.diff?.applied ?? []) {
      if (!item.block_id) continue;
      try {
        const { block, history } = await api.getBlock(item.block_id);
        const prev = history[history.length - 2];
        if (prev) {
          await api.rollbackBlock(item.block_id, {
            rev_id: prev.rev_id,
            expected_revision: block.revision,
          });
        }
      } catch (e: any) {
        setToast("撤销失败：" + e.message);
      }
    }
    await loadBlocks();
    setAgent({ diff: null, plan: null, planId: null });
    setToast("已撤销本次 agent 编辑");
  };

  return (
    <div className="agent-bar">
      <input
        placeholder="给 agent 的编辑指令（先出计划，确认后执行）"
        value={instruction}
        onChange={(e) => setInstruction(e.target.value)}
      />
      <button onClick={() => void plan()} disabled={agent.running}>
        生成计划
      </button>

      {agent.plan && (
        <div className="plan-panel">
          <div>
            <b>意图：</b>
            {agent.plan.intent}
          </div>
          <div>
            <b>影响块：</b>
            {agent.plan.affected_blocks?.join(", ")}
          </div>
          <ul>
            {(agent.plan.proposed_changes ?? []).map((c: any, i: number) => (
              <li key={i}>
                {c.tool} — {c.message || JSON.stringify(c.args)}
              </li>
            ))}
          </ul>
          <button onClick={() => void exec()} disabled={agent.running}>
            确认执行
          </button>
          <button onClick={() => setAgent({ plan: null, planId: null })}>取消</button>
        </div>
      )}

      {agent.diff && (
        <div className="diff-bar">
          <span>已应用 {agent.diff.applied?.length ?? 0} 个操作</span>
          <button onClick={() => void undo()}>撤销本次编辑</button>
        </div>
      )}
      {agent.error && <div className="error">{agent.error}</div>}
    </div>
  );
}
