/** API 客户端：所有 /api 请求自动携带 X-Local-Token（生产取自 HTML meta）。 */

import type { Anchor, DocSummary, NoteBlock } from "./types";

export type { Anchor, DocSummary, NoteBlock, SourceRef, SelectionHit } from "./types";

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
    let body: any = {};
    try {
      body = await resp.json();
    } catch {
      body = { error: String(resp.status) };
    }
    // 兼容两种错误形状：{error:"CODE",message,detail} 与 {error:{code,message,details}}
    const nested = body && typeof body.error === "object" && body.error !== null;
    const code = nested ? body.error.code : body.error;
    const message = nested ? body.error.message : body.message;
    const detail = nested ? body.error.details : body.detail;
    const fallback = resp.status === 409 ? "数据已被修改，请刷新后重试" : "请求失败 " + resp.status;
    const err = new Error(message || code || fallback);
    (err as any).code = code;
    (err as any).detail = detail;
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
  getManifest: (docId: string) => api.get<any>("/api/v1/docs/" + docId),
  deleteDoc: (docId: string) =>
    api.del<any>("/api/v1/docs/" + docId, { confirmed: true }),
  rebuildDoc: (docId: string) => api.post<{ task_id: string; doc_id: string }>(
    "/api/v1/docs/" + docId + "/rebuild", {}),
  refreshFigures: (docId: string) => api.post<{ figures: number; patched_blocks: number }>(
    "/api/v1/docs/" + docId + "/refresh-figures", {}),
  pdfUrl: (docId: string) => "/api/v1/docs/" + docId + "/pdf",
  figureUrl: (docId: string, figureId: string) => "/api/v1/docs/" + docId + "/figures/" + figureId,
  listBlocks: (docId: string, chapterId: string, includeDeleted = false) =>
    api.get<{ items: NoteBlock[]; next_cursor: string | null; data_revision: number }>(
      "/api/v1/docs/" + docId + "/chapters/" + chapterId + "/blocks?include_deleted=" + (includeDeleted ? 1 : 0)
    ),
  reviewQueue: (docId: string) =>
    api.get<{ items: NoteBlock[] }>("/api/v1/docs/" + docId + "/blocks?status=needs_review"),
  getBlock: (blockId: string) =>
    api.get<{ block: NoteBlock; history: any[] }>("/api/v1/blocks/" + blockId),
  createBlock: (docId: string, chapterId: string, body: any) =>
    api.post<NoteBlock>("/api/v1/docs/" + docId + "/chapters/" + chapterId + "/blocks", body),
  updateBlock: (blockId: string, body: any) => api.patch<NoteBlock>("/api/v1/blocks/" + blockId, body),
  deleteBlock: (blockId: string, body: any) => api.del<NoteBlock>("/api/v1/blocks/" + blockId, body),
  restoreBlock: (blockId: string, body: any) => api.post<NoteBlock>("/api/v1/blocks/" + blockId + "/restore", body),
  rollbackBlock: (blockId: string, body: any) => api.post<NoteBlock>("/api/v1/blocks/" + blockId + "/rollback", body),
  allowRegeneration: (blockId: string, body: any) =>
    api.post<NoteBlock>("/api/v1/blocks/" + blockId + "/allow-regeneration", body),
  reorder: (docId: string, chapterId: string, body: any) =>
    api.post<{ items: NoteBlock[] }>("/api/v1/docs/" + docId + "/chapters/" + chapterId + "/reorder", body),
  getAnchor: (anchorId: string) => api.get<Anchor>("/api/v1/anchors/" + anchorId),
  pageBlocks: (docId: string, page: number) =>
    api.get<any[]>("/api/v1/docs/" + docId + "/pages/" + page + "/blocks"),
  match: (docId: string, body: any) => api.post<{ hits: any[] }>("/api/v1/docs/" + docId + "/match", body),
  search: (docId: string, q: string) =>
    api.get<{ anchors: any[]; blocks: any[] }>(
      "/api/v1/search?doc_id=" + docId + "&q=" + encodeURIComponent(q)
    ),
  agentEdit: (url: string, body: any) => api.post<any>(url, body),
  generate: (docId: string, body: any) => api.post<{ task_id: string }>("/api/v1/docs/" + docId + "/generate", body),
  generateReports: (docId: string) => api.get<{ reports: any[] }>("/api/v1/docs/" + docId + "/generate-reports"),
  exportDoc: (body: any) => api.post<{ task_id: string }>("/api/v1/export", body),
  getTask: (taskId: string) => api.get<any>("/api/v1/tasks/" + taskId),
  cancelTask: (taskId: string) => api.post<any>("/api/v1/tasks/" + taskId + "/cancel"),
  listTasks: (docId?: string | null, limit = 12) =>
    api.get<{ items: any[] }>(
      "/api/v1/tasks?limit=" + limit + (docId ? "&doc_id=" + encodeURIComponent(docId) : "")),
  deleteTask: (taskId: string) => api.del<any>("/api/v1/tasks/" + taskId),
  models: () => api.get<any>("/api/v1/models"),
  testModel: (body: any) => api.post<any>("/api/v1/models/test", body),
  upsertProvider: (id: string, body: any) => api.put<any>("/api/v1/models/" + id, body),
  deleteProvider: (id: string) => api.del<any>("/api/v1/models/" + id),
  updateRoutes: (body: any) => api.put<any>("/api/v1/model-routes", body),
  usage: () => api.get<any>("/api/v1/usage"),
  setupStatus: () => api.get<any>("/api/v1/setup/status"),
  settings: () => api.get<any>("/api/v1/settings"),
  saveSettings: (body: any) => api.put<any>("/api/v1/settings", body),
};
