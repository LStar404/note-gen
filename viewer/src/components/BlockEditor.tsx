import { useEffect, useState } from "react";
import { api, type NoteBlock } from "../api/client";
import { renderMarkdown } from "../lib/markdown";
import { useStore } from "../state/store";

export default function BlockEditor({ block, mode }: { block: NoteBlock; mode: "source" | "preview" }) {
  const docId = useStore((s) => s.docId);
  const loadBlocks = useStore((s) => s.loadBlocks);
  const setToast = useStore((s) => s.setToast);
  const setDraft = useStore((s) => s.setDraft);
  const [title, setTitle] = useState(block.title);
  const [content, setContent] = useState(block.content);
  const [error, setError] = useState<string | null>(null);

  const save = async () => {
    try {
      await api.updateBlock(block.id, {
        title,
        content,
        expected_revision: block.revision,
        message: "人工编辑",
        author: "human",
      });
      setError(null);
      setDraft(null);
      setToast("已保存（revision " + (block.revision + 1) + "）");
      await loadBlocks();
    } catch (e: any) {
      if (e.code === "REVISION_CONFLICT") {
        setError("数据已被修改（当前 revision " + e.detail?.current_revision + "），请刷新后重试");
      } else {
        setError(e.message);
      }
    }
  };

  useEffect(() => {
    const onSave = () => void save();
    window.addEventListener("note-gen:save", onSave);
    return () => window.removeEventListener("note-gen:save", onSave);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [title, content, block]);

  if (mode === "preview") {
    return (
      <div
        className="block-preview-view"
        dangerouslySetInnerHTML={{
          __html: renderMarkdown(content, docId ?? "", {}),
        }}
      />
    );
  }

  return (
    <div className="block-editor">
      <input
        value={title}
        onChange={(e) => {
          setTitle(e.target.value);
          setDraft(e.target.value);
        }}
      />
      <textarea
        value={content}
        rows={10}
        onChange={(e) => {
          setContent(e.target.value);
          setDraft(e.target.value);
        }}
      />
      {error && <div className="error">{error}</div>}
      <div className="editor-actions">
        <button onClick={() => void save()}>保存（Ctrl+S）</button>
        <button
          onClick={() => {
            setTitle(block.title);
            setContent(block.content);
            setError(null);
            setDraft(null);   // 还原后不再是草稿态：否则切换视图误报「未保存草稿」
          }}
        >
          还原
        </button>
      </div>
    </div>
  );
}
