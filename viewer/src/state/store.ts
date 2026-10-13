/** 全局状态（规范 7.3）：zustand 单一 store。 */

import { create } from "zustand";
import { api, type DocSummary, type NoteBlock, type Anchor } from "../api/client";

const anchorCache = new Map<string, Anchor>();
// 在途请求合并：几十张卡片同时挂载时，同一锚点会被重复拉取（无 pending 表时是请求风暴）
const anchorPending = new Map<string, Promise<void>>();

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
  zoomBlockId: string | null;
  pdfPage: number;
  selectionHits: any[];
  highlightAnchor: Anchor | null;
  agent: AgentState;
  view: "card" | "source" | "preview";
  dirtyDraft: string | null;
  settingsOpen: boolean;
  setupOpen: boolean;
  onboardOpen: boolean;
  openOnboard: (open: boolean) => void;
  reviewOpen: boolean;
  reviewItems: NoteBlock[];
  reviewCount: number;
  // 块类型注册表（学科模板 + 用户自定义）：卡片图标/名称与生成同一事实源
  typeInfo: Record<string, { name: string; icon: string }>;
  // 单块 AI 编辑 / 知识点关联
  aiEditBlockId: string | null;
  linkBlockId: string | null;
  openAiEdit: (id: string) => void;
  closeAiEdit: () => void;
  openLinks: (id: string) => void;
  closeLinks: () => void;
  toast: string | null;
  anchors: Record<string, Anchor>;

  fetchAnchors: (ids: string[]) => Promise<void>;
  loadDocs: () => Promise<void>;
  selectDoc: (docId: string) => Promise<void>;
  selectChapter: (chapterId: string) => Promise<void>;
  loadBlocks: () => Promise<void>;
  selectBlock: (blockId: string | null) => void;
  openZoom: (blockId: string) => void;
  closeZoom: () => void;
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
  refreshReviewCount: () => Promise<void>;
}

export const useStore = create<Store>((set, get) => ({
  docs: [],
  docId: null,
  manifest: null,
  chapterId: null,
  blocks: [],
  selectedBlockId: null,
  zoomBlockId: null,
  pdfPage: 1,
  selectionHits: [],
  highlightAnchor: null,
  agent: { plan: null, planId: null, running: false, diff: null, error: null },
  view: "card",
  dirtyDraft: null,
  settingsOpen: false,
  setupOpen: false,
  onboardOpen: false,
  reviewOpen: false,
  reviewItems: [],
  reviewCount: 0,
  typeInfo: {},
  aiEditBlockId: null,
  linkBlockId: null,
  openAiEdit: (id) => set({ aiEditBlockId: id }),
  closeAiEdit: () => set({ aiEditBlockId: null }),
  openLinks: (id) => set({ linkBlockId: id }),
  closeLinks: () => set({ linkBlockId: null }),
  toast: null,
  anchors: {},

  fetchAnchors: async (ids) => {
    const wanted = ids.filter((id) => id && !anchorCache.has(id));
    if (!wanted.length) return;
    await Promise.all(
      wanted.map((id) => {
        let p = anchorPending.get(id);
        if (!p) {
          p = api
            .getAnchor(id)
            .then((a) => {
              anchorCache.set(id, a);
            })
            .catch(() => {
              /* 锚点缺失时徽标显示占位 */
            })
            .finally(() => {
              anchorPending.delete(id);
            });
          anchorPending.set(id, p);
        }
        return p;
      })
    );
    set({ anchors: Object.fromEntries(anchorCache) });
  },
  loadDocs: async () => {
    const { docs } = await api.listDocs();
    set({ docs });
  },
  selectDoc: async (docId) => {
    const manifest = await api.getManifest(docId);
    const chapterId = manifest.chapters?.[0]?.chapter_id ?? null;
    set({ docId, manifest, chapterId, blocks: [], selectedBlockId: null, zoomBlockId: null, pdfPage: 1 });
    if (chapterId) await get().loadBlocks();
  },
  selectChapter: async (chapterId) => {
    set({ chapterId, blocks: [], selectedBlockId: null, zoomBlockId: null });
    await get().loadBlocks();
  },
  loadBlocks: async () => {
    const { docId, chapterId } = get();
    if (!docId || !chapterId) return;
    const { items } = await api.listBlocks(docId, chapterId);
    set({ blocks: items });
  },
  selectBlock: (blockId) => set({ selectedBlockId: blockId }),
  openZoom: (blockId) => set({ zoomBlockId: blockId }),
  closeZoom: () => set({ zoomBlockId: null }),
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
  openOnboard: (open) => set({ onboardOpen: open }),
  openReview: async (open) => {
    if (!open) {
      set({ reviewOpen: false });
      void get().refreshReviewCount();   // 审核完关窗即刷新角标
      return;
    }
    const { docId } = get();
    if (!docId) return;
    const { items } = await api.reviewQueue(docId);
    set({ reviewOpen: true, reviewItems: items, reviewCount: items.length });
  },
  refreshReviewCount: async () => {
    // 未审核角标（生成完/切书/关窗后调用）：失败不打扰主界面
    const { docId } = get();
    if (!docId) return set({ reviewCount: 0 });
    try {
      const { items } = await api.reviewQueue(docId);
      set({ reviewCount: items.length });
    } catch {
      /* 忽略角标刷新失败 */
    }
  },
}));
