import { useEffect } from "react";
import { api } from "./api/client";
import Toolbar from "./components/Toolbar";
import PdfPane from "./pdf/PdfPane";
import NotesPane from "./components/NotesPane";
import BlockDetailPanel from "./components/BlockDetailPanel";
import SettingsPanel from "./components/SettingsPanel";
import SetupGuide from "./components/SetupGuide";
import ReviewQueue from "./components/ReviewQueue";
import { useStore } from "./state/store";

export default function App() {
  // 主题：跟随 localStorage，首次进入用浅色
  useEffect(() => {
    const saved = localStorage.getItem("note-gen-theme") || "light";
    document.documentElement.setAttribute("data-theme", saved);
  }, []);

  const toast = useStore((s) => s.toast);
  const blocks = useStore((s) => s.blocks);
  const selectedBlockId = useStore((s) => s.selectedBlockId);
  const selectBlock = useStore((s) => s.selectBlock);
  const setView = useStore((s) => s.setView);
  const view = useStore((s) => s.view);
  const openSettings = useStore((s) => s.openSettings);
  const openReview = useStore((s) => s.openReview);
  const dirtyDraft = useStore((s) => s.dirtyDraft);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      // j/k/e/s 单键在输入框/输入法组合输入期间不得触发（7.3）
      const composing = e.isComposing || e.key === "Process" || e.key === "Unidentified";
      const typing =
        !!target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.isContentEditable ||
          composing);

      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        window.dispatchEvent(new CustomEvent("note-gen:save"));
        return;
      }
      if (e.key === "Escape") {
        openSettings(false);
        useStore.getState().openSetup(false);
        openReview(false);
        useStore.getState().setAgent({ plan: null, planId: null });
        return;
      }
      if (typing) return;

      const idx = blocks.findIndex((b) => b.id === selectedBlockId);
      if (e.key === "j") {
        e.preventDefault();
        const next = blocks[Math.min(blocks.length - 1, idx + 1)] ?? blocks[0];
        if (next) selectBlock(next.id);
      } else if (e.key === "k") {
        e.preventDefault();
        const prev = blocks[Math.max(0, idx - 1)] ?? blocks[0];
        if (prev) selectBlock(prev.id);
      } else if (e.key === "e") {
        setView("source");
      } else if (e.key === "s") {
        setView(view === "source" ? "preview" : "source");
      } else if (e.key === "/") {
        e.preventDefault();
        document.getElementById("global-search")?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [blocks, selectedBlockId, view]);

  // 配置指南（ADR D35）：首次使用且未就绪时自动弹出；手动关闭后不再打扰
  useEffect(() => {
    api
      .setupStatus()
      .then((s: any) => {
        if (s.first_run && !s.ready && !localStorage.getItem("note-gen-setup-seen")) {
          useStore.getState().openSetup(true);
        }
      })
      .catch(() => undefined);
  }, []);

  // 脏草稿守卫（7.3）：关页前提示
  useEffect(() => {
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      if (dirtyDraft !== null) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [dirtyDraft]);

  return (
    <div className="app">
      <Toolbar />
      <main className="columns">
        <PdfPane />
        <NotesPane />
        <BlockDetailPanel />
      </main>
      <SettingsPanel />
      <SetupGuide />
      <ReviewQueue />
      {toast && <div className="toast">{toast}</div>}
    </div>
  );
}
