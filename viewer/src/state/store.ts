/** 全局状态（规范 7.3）：zustand 单一 store。 */

import { create } from "zustand";
import { api, type DocSummary, type NoteBlock, type Anchor } from "../api/client";

const anchorCache = new Map<string, Anchor>();

export interface AgentState {
  plan: any | null;
  planId: string | null;
  running: boolean;
  diff: any | null;
  error: string | null;
}

interface Store {
  docs: DocSummary[];
  docId: string | null;
  manifest: any | null;
  chapterId: string | null;
  blocks: NoteBlock[];
  selectedBlockId: string | null;
  pdfPage: number;
  selectionHits: any[];
  highlightAnchor: Anchor | null;
  agent: AgentState;
  view: "card" | "source" | "preview";
  dirtyDraft: string | null;
  settingsOpen: boolean;
  setupOpen: boolean;
  reviewOpen: boolean;
  reviewItems: NoteBlock[];
  toast: string | null;
  anchors: Record<string, Anchor>;

  fetchAnchors: (ids: string[]) => Promise<void>;
  loadDocs: () => Promise<void>;
  selectDoc: (docId: string) => Promise<void>;
  selectChapter: (chapterId: string) => Promise<void>;
  loadBlocks: () => Promise<void>;
  selectBlock: (blockId: string | null) => void;
  setPdfPage: (page: number) => void;
  setSelectionHits: (hits: any[]) => void;
  setHighlight: (anchor: Anchor | null) => void;
  setView: (view: "card" | "source" | "preview") => void;
  setDraft: (draft: string | null) => void;
  setAgent: (patch: Partial<AgentState>) => void;
  setToast: (msg: string | null) => void;
  openSettings: (open: boolean) => void;
  openSetup: (open: boolean) => void;
  openReview: (open: boolean) => Promise<void>;
}

export const useStore = create<Store>((set, get) => ({
  docs: [],
  docId: null,
  manifest: null,
  chapterId: null,
  blocks: [],
  selectedBlockId: null,
  pdfPage: 1,
  selectionHits: [],
  highlightAnchor: null,
  agent: { plan: null, planId: null, running: false, diff: null, error: null },
  view: "card",
  dirtyDraft: null,
  settingsOpen: false,
  setupOpen: false,
  reviewOpen: false,
  reviewItems: [],
  toast: null,
  anchors: {},

  fetchAnchors: async (ids) => {
    const missing = ids.filter((id) => id && !anchorCache.has(id));
    await Promise.all(
      missing.map(async (id) => {
        try {
          anchorCache.set(id, await api.getAnchor(id));
        } catch {
          /* 锚点缺失时徽标显示占位 */
        }
      })
    );
    if (missing.length) set({ anchors: Object.fromEntries(anchorCache) });
  },
  loadDocs: async () => {
    const { docs } = await api.listDocs();
    set({ docs });
  },
  selectDoc: async (docId) => {
    const manifest = await api.getManifest(docId);
    const chapterId = manifest.chapters?.[0]?.chapter_id ?? null;
    set({ docId, manifest, chapterId, blocks: [], selectedBlockId: null, pdfPage: 1 });
    if (chapterId) await get().loadBlocks();
  },
  selectChapter: async (chapterId) => {
    set({ chapterId, blocks: [], selectedBlockId: null });
    await get().loadBlocks();
  },
  loadBlocks: async () => {
    const { docId, chapterId } = get();
    if (!docId || !chapterId) return;
    const { items } = await api.listBlocks(docId, chapterId);
    set({ blocks: items });
  },
  selectBlock: (blockId) => set({ selectedBlockId: blockId }),
  setPdfPage: (page) => set({ pdfPage: page }),
  setSelectionHits: (hits) => set({ selectionHits: hits }),
  setHighlight: (anchor) => set({ highlightAnchor: anchor }),
  setView: (view) => set({ view }),
  setDraft: (draft) => set({ dirtyDraft: draft }),
  setAgent: (patch) => set({ agent: { ...get().agent, ...patch } }),
  setToast: (msg) => {
    set({ toast: msg });
    if (msg) setTimeout(() => set({ toast: null }), 3000);
  },
  openSettings: (open) => set({ settingsOpen: open }),
  openSetup: (open) => set({ setupOpen: open }),
  openReview: async (open) => {
    if (!open) return set({ reviewOpen: false });
    const { docId } = get();
    if (!docId) return;
    const { items } = await api.reviewQueue(docId);
    set({ reviewOpen: true, reviewItems: items });
  },
}));
