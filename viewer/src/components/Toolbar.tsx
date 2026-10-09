import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useStore } from "../state/store";

export default function Toolbar() {
  const docs = useStore((s) => s.docs);
  const docId = useStore((s) => s.docId);
  const manifest = useStore((s) => s.manifest);
  const chapterId = useStore((s) => s.chapterId);
  const loadDocs = useStore((s) => s.loadDocs);
  const selectDoc = useStore((s) => s.selectDoc);
  const selectChapter = useStore((s) => s.selectChapter);
  const openSettings = useStore((s) => s.openSettings);
  const openReview = useStore((s) => s.openReview);
  const setToast = useStore((s) => s.setToast);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<any | null>(null);

  useEffect(() => {
    void loadDocs();
  }, []);

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
      setToast("导出任务已启动：" + task_id);
    } catch (e: any) {
      setToast("导出失败：" + e.message);
    }
  };

  const doGenerate = async () => {
    if (!docId) return;
    try {
      const { task_id } = await api.generate(docId, { chapter_id: chapterId });
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
      setToast("导入任务已启动：" + resp.task_id);
      await loadDocs();
    } catch (e: any) {
      setToast("导入失败：" + e.message);
    }
  };

  return (
    <header className="toolbar">
      <select value={docId ?? ""} onChange={(e) => void selectDoc(e.target.value)}>
        <option value="">— 选择教材 —</option>
        {docs.map((d) => (
          <option key={d.doc_id} value={d.doc_id}>
            {d.title}
          </option>
        ))}
      </select>

      <select value={chapterId ?? ""} onChange={(e) => void selectChapter(e.target.value)}>
        {(manifest?.chapters ?? []).map((c: any) => (
          <option key={c.chapter_id} value={c.chapter_id}>
            {c.title}
          </option>
        ))}
      </select>

      <label className="file-button">
        导入 PDF
        <input type="file" accept="application/pdf" hidden onChange={(e) => void importPdf(e.target.files?.[0] ?? null)} />
      </label>

      <button onClick={doGenerate}>生成本章</button>
      <button onClick={() => void doExport("md")}>导出 md</button>
      <button onClick={() => void doExport("tex")}>导出 tex</button>
      <button onClick={() => void doExport("md-obsidian")}>导出 Obsidian</button>
      <button onClick={() => void openReview(true)}>待审核</button>
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
                <div key={a.anchor_id} className="search-item" onClick={() => void useStore.getState().selectBlock(null)}>
                  p.{a.page} {a.content.slice(0, 40)}
                </div>
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
    </header>
  );
}
