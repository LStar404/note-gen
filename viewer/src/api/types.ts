/** 共享 API 类型（规范 4.x 字段名与后端一致）。 */

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

export interface SelectionHit {
  anchor_id: string;
  page: number;
  bbox: [number, number, number, number];
  content: string;
  scores: { cover_anchor: number; cover_selection: number; iou: number };
  match: "geometry" | "text";
  blocks: { block_id: string; title: string; type: string; summary: string; status: string }[];
}

/** 块渲染顺序（不变量 I9）：order 升序，同 order 按 block_id 字典序。 */
export function blockOrderKey(b: NoteBlock): [number, string] {
  return [Number(b.order) || 0, String(b.id)];
}

export function sortBlocks(blocks: NoteBlock[]): NoteBlock[] {
  return [...blocks].sort((a, b) => {
    const [ao, ai] = blockOrderKey(a);
    const [bo, bi] = blockOrderKey(b);
    return ao === bo ? (ai < bi ? -1 : ai > bi ? 1 : 0) : ao - bo;
  });
}

export const RELATION_CN: Record<string, string> = {
  defines: "定义",
  states: "陈述",
  proves: "证明",
  motivates: "动机",
  illustrates: "图示",
  summarizes: "归纳",
};

export const STATUS_CN: Record<string, string> = {
  ai_generated: "AI 生成",
  hybrid: "混合",
  manually_edited: "人工编辑",
  needs_review: "待审核",
};
