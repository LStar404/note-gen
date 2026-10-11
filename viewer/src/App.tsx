import { useEffect, useRef, useState } from "react";
import { api } from "./api/client";
import Toolbar from "./components/Toolbar";
import PdfPane from "./pdf/PdfPane";
import NotesPane from "./components/NotesPane";
import BlockDetailPanel from "./components/BlockDetailPanel";
import SettingsPanel from "./components/SettingsPanel";
import SetupGuide from "./components/SetupGuide";
import ReviewQueue from "./components/ReviewQueue";
import { useStore } from "./state/store";

// 三栏初始列宽（px）：随窗口宽度给个合理默认
function initialWidths(): [number, number] {
  try {
    const v = JSON.parse(localStorage.getItem("note-gen-col-widths") || "null");
    if (Array.isArray(v) && v.length === 2 && isFinite(v[0]) && isFinite(v[1])) {
      return [Math.max(180, Number(v[0])), Math.max(180, Number(v[1]))];
    }
  } catch {
    /* 忽略损坏的本地设置 */
  }
  return [Math.round(window.innerWidth * 0.36), Math.round(window.innerWidth * 0.32)];
}

export default function App() {
  // 主题：跟随 localStorage，首次进入用浅色
  useEffect(() => {
    const saved = localStorage.getItem("note-gen-theme") || "light";
    document.documentElement.setAttribute("data-theme", saved);
  }, []);

  // 三栏宽度可拖拽调节（列宽记忆到 localStorage）
  const [widths, setWidths] = useState<[number, number]>(initialWidths);
  const colsRef = useRef<HTMLElement>(null);
  useEffect(() => {
    localStorage.setItem("note-gen-col-widths", JSON.stringify(widths));
  }, [widths]);
  const startResize = (idx: 0 | 1) => (e: React.PointerEvent) => {
    e.preventDefault();
    const move = (ev: PointerEvent) => {
      const rect = colsRef.current?.getBoundingClientRect();
      if (!rect) return;
      const x = ev.clientX - rect.left;
      setWidths(([w0, w1]) => {
        const min = 180;                                   // 每栏最小宽度
        const rest = rect.width - w0 - w1 - 40;            // 第三栏留出的余量
        if (idx === 0) return [Math.min(Math.max(x, min), rect.width - w1 - min - 40), w1];
        return [w0, Math.min(Math.max(x - w0, min), rect.width - w0 - min - 40) || rest];
      });
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      document.body.style.cursor = "";
    };
    document.body.style.cursor = "col-resize";
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };

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
      <main className="columns" ref={colsRef}>
        <div className="col" style={{ flex: "0 0 " + widths[0] + "px" }}>
          <PdfPane />
        </div>
        <div className="splitter" title="拖动调节栏宽" onPointerDown={startResize(0)} />
        <div className="col" style={{ flex: "0 0 " + widths[1] + "px" }}>
          <NotesPane />
        </div>
        <div className="splitter" title="拖动调节栏宽" onPointerDown={startResize(1)} />
        <div className="col" style={{ flex: "1 1 0" }}>
          <BlockDetailPanel />
        </div>
      </main>
      <SettingsPanel />
      <SetupGuide />
      <ReviewQueue />
      {toast && <div className="toast">{toast}</div>}
    </div>
  );
}
