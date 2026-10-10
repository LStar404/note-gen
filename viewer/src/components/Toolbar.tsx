import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { useStore } from "../state/store";

// 任务进度可读化（9.2 进度透传）：提纲 2/6 批、填充 3/20、生成 ch01
function progressText(p: any): string {
  if (!p) return "";
  if (p.stage === "outline" && p.batches) return "提纲 " + (p.batch ?? 0) + "/" + p.batches + " 批";
  if (p.stage === "outline_done") return "提纲完成 " + (p.count ?? 0) + " 项";
  if (p.stage === "fill" && p.total) return "填充 " + (p.done ?? 0) + "/" + p.total;
  if (p.stage === "generate") return p.chapter_id ? "生成 " + p.chapter_id : "生成中";
  return String(p.stage ?? "");
}

export default function Toolbar() {
  const docs = useStore((s) => s.docs);
  const docId = useStore((s) => s.docId);
  const manifest = useStore((s) => s.manifest);
  const chapterId = useStore((s) => s.chapterId);
  const dirtyDraft = useStore((s) => s.dirtyDraft);
  const setDraft = useStore((s) => s.setDraft);
  const loadDocs = useStore((s) => s.loadDocs);
  const selectDoc = useStore((s) => s.selectDoc);
  const selectChapter = useStore((s) => s.selectChapter);
  const openSettings = useStore((s) => s.openSettings);
  const openSetup = useStore((s) => s.openSetup);
  const openReview = useStore((s) => s.openReview);
  const setToast = useStore((s) => s.setToast);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<any | null>(null);
  const [tasks, setTasks] = useState<any[]>([]);
  const timersRef = useRef<Set<number>>(new Set());

  // 组件卸载时清掉所有轮询定时器，避免僵尸请求/内存泄漏
  useEffect(() => {
    const timers = timersRef.current;
    return () => {
      timers.forEach((t) => window.clearInterval(t));
      timers.clear();
    };
  }, []);

  // 任务进度/取消（9.2 /tasks/{id}）
  const track = (taskId: string, kind: string) => {
    setTasks((list) => [...list, { task_id: taskId, kind, status: "queued", progress: {} }]);
    const timer = window.setInterval(async () => {
      try {
        const t = await api.getTask(taskId);
        setTasks((list) => list.map((x) => (x.task_id === taskId ? { ...x, ...t } : x)));
        if (["succeeded", "failed", "partial", "cancelled", "interrupted"].includes(t.status)) {
          window.clearInterval(timer);
          timersRef.current.delete(timer);
        }
      } catch {
        window.clearInterval(timer);
        timersRef.current.delete(timer);
      }
    }, 1000);
    timersRef.current.add(timer);
  };

  useEffect(() => {
    void loadDocs();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 脏草稿守卫（7.3）：切章/换教材必须提示，不能静默丢弃
  const guardDirty = (): boolean => {
    if (dirtyDraft === null) return true;
    const ok = window.confirm("当前块有未保存草稿，切换将丢弃草稿，继续吗？");
    if (ok) setDraft(null);
    return ok;
  };

  const runSearch = async () => {
    if (!docId || !query.trim()) return;
    try {
      setResults(await api.search(docId, query.trim()));
    } catch (e: any) {
      setToast("检索失败：" + e.message);
    }
  };

  const doExport = async (format: string) => {
    if (!docId) return;
    try {
      const { task_id } = await api.exportDoc({ doc_id: docId, format });
      track(task_id, "export");
      setToast("导出任务已启动：" + task_id);
    } catch (e: any) {
      setToast("导出失败：" + e.message);
    }
  };

  const doGenerate = async () => {
    if (!docId) return;
    try {
      const { task_id } = await api.generate(docId, { chapter_id: chapterId });
      track(task_id, "generate");
      setToast("生成任务已启动：" + task_id);
    } catch (e: any) {
      setToast("生成失败：" + e.message);
    }
  };

  const importPdf = async (file: File | null) => {
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    try {
      const resp = await api.post<any>("/api/v1/docs", form);
      track(resp.task_id, "import");
      setToast("导入任务已启动：" + resp.task_id);
      await loadDocs();
    } catch (e: any) {
      setToast("导入失败：" + e.message);
    }
  };

  const [theme, setTheme] = useState<string>(
    () => localStorage.getItem("note-gen-theme") || "light"
  );

  const toggleTheme = () => {
    const next = theme === "light" ? "dark" : "light";
    setTheme(next);
    localStorage.setItem("note-gen-theme", next);
    document.documentElement.setAttribute("data-theme", next);
  };

  return (
    <header className="toolbar">
      <div className="brand">
        <span className="logo">📒</span>
        <span>note-gen</span>
      </div>
      <button className="icon" onClick={toggleTheme} title="切换明暗主题">
        {theme === "light" ? "🌙" : "☀️"}
      </button>
      <select
        value={docId ?? ""}
        onChange={(e) => {
          if (!guardDirty()) return;
          void selectDoc(e.target.value);
        }}
      >
        <option value="">— 选择教材 —</option>
        {docs.map((d) => (
          <option key={d.doc_id} value={d.doc_id}>{d.title}</option>
        ))}
      </select>

      <select
        value={chapterId ?? ""}
        onChange={(e) => {
          if (!guardDirty()) return;
          void selectChapter(e.target.value);
        }}
      >
        {(manifest?.chapters ?? []).map((c: any) => (
          <option key={c.chapter_id} value={c.chapter_id}>{c.title}</option>
        ))}
      </select>

      <label className="file-button primary">
        导入 PDF
        <input type="file" accept="application/pdf" hidden onChange={(e) => void importPdf(e.target.files?.[0] ?? null)} />
      </label>

      {docId && (
        <button
          className="danger"
          title="删除该教材（移入回收站 data/.trash，可找回）"
          onClick={() => {
            const doc = docs.find((d) => d.doc_id === docId);
            const chapters = doc?.chapters?.length ?? 0;
            const ok = window.confirm(
              "确认删除教材《" + (doc?.title ?? docId) + "》？\n\n" +
              "· 将删除其全部笔记块、锚点、历史与图资源（约 " + chapters + " 章）\n" +
              "· 移入回收站 data/.trash，可在磁盘上找回\n" +
              "· 此操作会立即从列表中移除该教材"
            );
            if (!ok) return;
            void api
              .deleteDoc(docId)
              .then(async () => {
                setToast("已删除《" + (doc?.title ?? docId) + "》，已移入回收站（data/.trash 可找回）");
                useStore.setState({ docId: null, manifest: null, chapterId: null,
                                    blocks: [], selectedBlockId: null });
                await loadDocs();
              })
              .catch((e: any) => setToast("删除失败：" + e.message));
          }}
        >
          删除教材
        </button>
      )}

      <button onClick={doGenerate}>生成本章</button>
      <button onClick={() => void doExport("md")}>导出 md</button>
      <button onClick={() => void doExport("tex")}>导出 tex</button>
      <button onClick={() => void doExport("md-obsidian")}>导出 Obsidian</button>
      <button onClick={() => void openReview(true)}>待审核</button>
      <button onClick={() => openSetup(true)} title="配置指南：环境自检与首次使用引导">配置指南</button>
      <button onClick={() => openSettings(true)}>设置</button>

      <div className="search-box">
        <input
          id="global-search"
          placeholder="全局检索（/）"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && void runSearch()}
        />
        {results && (
          <div className="search-results">
            <div className="search-group">
              <b>锚点</b>
              {results.anchors.map((a: any) => (
                <div key={a.anchor_id} className="search-item">p.{a.page} {a.content.slice(0, 40)}</div>
              ))}
            </div>
            <div className="search-group">
              <b>块</b>
              {results.blocks.map((b: any) => (
                <div key={b.block_id} className="search-item" onClick={() => useStore.getState().selectBlock(b.block_id)}>
                  {b.title}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {tasks.length > 0 && (
        <div className="task-tray">
          {tasks.map((t) => (
            <div key={t.task_id} className="task-row" title={t.error?.message ?? ""}>
              <span>
                {t.kind} · {t.status}
                {t.progress?.stage ? " · " + progressText(t.progress) : ""}
                {t.status === "failed" && t.error?.message ? " · " + t.error.message : ""}
              </span>
              {["queued", "running"].includes(t.status) && (
                <button
                  onClick={() =>
                    void api.cancelTask(t.task_id).then(() => setToast("已请求取消 " + t.task_id))
                  }
                >
                  取消
                </button>
              )}
            </div>
          ))}
        </div>
      )}
    </header>
  );
}
