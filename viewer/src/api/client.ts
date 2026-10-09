/** API 客户端：所有 /api 请求自动携带 X-Local-Token（生产取自 HTML meta）。 */

export interface SourceRef {
  anchor_id: string;
  quote: string;
  relation: "defines" | "states" | "proves" | "motivates" | "illustrates" | "summarizes";
}

export interface NoteBlock {
  id: string;
  doc_id: string;
  chapter_id: string;
  type: string;
  title: string;
  content: string;
  content_format: "md_latex" | "tex" | "plain";
  order: number;
  heading_anchor_id: string | null;
  sources: SourceRef[];
  links: string[];
  status: "ai_generated" | "hybrid" | "manually_edited" | "needs_review";
  basis: "anchored" | "synthesis";
  tags: string[];
  created_by: "ai" | "human" | "agent";
  generation_protected: boolean;
  revision: number;
  created_at: string;
  updated_at: string;
  deleted_at: string | null;
}

export interface Anchor {
  anchor_id: string;
  doc_id: string;
  chapter_id: string;
  page: number;
  reading_order: number;
  bbox: [number, number, number, number];
  content: string;
  content_type: string;
  heading_level: number;
  note_block_ids: string[];
}

export interface DocSummary {
  doc_id: string;
  title: string;
  profile: string;
  chapters: { chapter_id: string; title: string; page_start: number; page_end: number }[];
  data_revision: number;
  updated_at: string;
}

function token(): string {
  const meta = document.querySelector('meta[name="local-token"]');
  return meta ? meta.getAttribute("content") || "" : "";
}

async function request<T>(method: string, url: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { "X-Local-Token": token() };
  let payload: BodyInit | undefined;
  if (body instanceof FormData) {
    payload = body;
  } else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  const resp = await fetch(url, { method, headers, body: payload });
  if (!resp.ok) {
    let detail: any = {};
    try {
      detail = await resp.json();
    } catch {
      detail = { error: String(resp.status) };
    }
    const err = new Error(detail.message || detail.error || `请求失败 ${resp.status}`);
    (err as any).code = detail.error;
    (err as any).detail = detail.detail;
    (err as any).status = resp.status;
    throw err;
  }
  return (await resp.json()) as T;
}

export const api = {
  get: <T>(url: string) => request<T>("GET", url),
  post: <T>(url: string, body?: unknown) => request<T>("POST", url, body),
  patch: <T>(url: string, body?: unknown) => request<T>("PATCH", url, body),
  put: <T>(url: string, body?: unknown) => request<T>("PUT", url, body),
  del: <T>(url: string, body?: unknown) => request<T>("DELETE", url, body),

  listDocs: () => api.get<{ docs: DocSummary[] }>("/api/v1/docs"),
  getManifest: (docId: string) => api.get<any>(`/api/v1/docs/${docId}`),
  pdfUrl: (docId: string) => `/api/v1/docs/${docId}/pdf`,
  figureUrl: (docId: string, figureId: string) =>
    `/api/v1/docs/${docId}/figures/${figureId}`,
  listBlocks: (docId: string, chapterId: string, includeDeleted = false) =>
    api.get<{ items: NoteBlock[]; next_cursor: string | null; data_revision: number }>(
      `/api/v1/docs/${docId}/chapters/${chapterId}/blocks?include_deleted=${includeDeleted ? 1 : 0}`
    ),
  reviewQueue: (docId: string) =>
    api.get<{ items: NoteBlock[] }>(`/api/v1/docs/${docId}/blocks?status=needs_review`),
  getBlock: (blockId: string) =>
    api.get<{ block: NoteBlock; history: any[] }>(`/api/v1/blocks/${blockId}`),
  createBlock: (docId: string, chapterId: string, body: any) =>
    api.post<NoteBlock>(`/api/v1/docs/${docId}/chapters/${chapterId}/blocks`, body),
  updateBlock: (blockId: string, body: any) => api.patch<NoteBlock>(`/api/v1/blocks/${blockId}`, body),
  deleteBlock: (blockId: string, body: any) => api.del<NoteBlock>(`/api/v1/blocks/${blockId}`, body),
  restoreBlock: (blockId: string, body: any) => api.post<NoteBlock>(`/api/v1/blocks/${blockId}/restore`, body),
  rollbackBlock: (blockId: string, body: any) => api.post<NoteBlock>(`/api/v1/blocks/${blockId}/rollback`, body),
  allowRegeneration: (blockId: string, body: any) =>
    api.post<NoteBlock>(`/api/v1/blocks/${blockId}/allow-regeneration`, body),
  reorder: (docId: string, chapterId: string, body: any) =>
    api.post<{ items: NoteBlock[] }>(`/api/v1/docs/${docId}/chapters/${chapterId}/reorder`, body),
  getAnchor: (anchorId: string) => api.get<Anchor>(`/api/v1/anchors/${anchorId}`),
  pageBlocks: (docId: string, page: number) =>
    api.get<any[]>(`/api/v1/docs/${docId}/pages/${page}/blocks`),
  match: (docId: string, body: any) =>
    api.post<{ hits: any[] }>(`/api/v1/docs/${docId}/match`, body),
  search: (docId: string, q: string) =>
    api.get<{ anchors: any[]; blocks: any[] }>(`/api/v1/search?doc_id=${docId}&q=${encodeURIComponent(q)}`),
  agentEdit: (url: string, body: any) => api.post<any>(url, body),
  generate: (docId: string, body: any) => api.post<{ task_id: string }>(`/api/v1/docs/${docId}/generate`, body),
  exportDoc: (body: any) => api.post<{ task_id: string }>("/api/v1/export", body),
  getTask: (taskId: string) => api.get<any>(`/api/v1/tasks/${taskId}`),
  cancelTask: (taskId: string) => api.post<any>(`/api/v1/tasks/${taskId}/cancel`),
  models: () => api.get<any>("/api/v1/models"),
  testModel: (body: any) => api.post<any>("/api/v1/models/test", body),
  upsertProvider: (id: string, body: any) => api.put<any>(`/api/v1/models/${id}`, body),
  deleteProvider: (id: string) => api.del<any>(`/api/v1/models/${id}`),
  updateRoutes: (body: any) => api.put<any>("/api/v1/model-routes", body),
  usage: () => api.get<any>("/api/v1/usage"),
};
