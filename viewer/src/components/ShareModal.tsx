import { useEffect, useState } from "react";

import { api } from "../api/client";
import {
  buildPlainText,
  buildShareHtml,
  copyText,
  downloadText,
  type SharePayload,
} from "../lib/share";
import { useStore } from "../state/store";

/** 分享：把当前章节（或整本）变成「能直接发出去」的东西——全部本地生成，不经过任何服务器。
 *
 * Markdown 贴进 Notion/Obsidian 会渲染公式；单文件 HTML 连公式字体都内嵌，对方断网也能看。
 */
export default function ShareModal({ onClose }: { onClose: () => void }) {
  const docId = useStore((s) => s.docId);
  const chapterId = useStore((s) => s.chapterId);
  const setToast = useStore((s) => s.setToast);
  const [scope, setScope] = useState<"chapter" | "book">("chapter");
  const [data, setData] = useState<SharePayload | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    if (!docId) return;
    setData(null);
    setErr("");
    void api
      .shareDoc(docId, scope === "chapter" ? chapterId : null)
      .then((d: SharePayload) => setData(d))
      .catch((e: any) => setErr(String(e?.message ?? e)));
  }, [docId, chapterId, scope]);

  const base = (data?.filename || "share").replace(/\.(md)$/i, "") || "share";
  const sizeKb = data ? Math.round(new Blob([data.markdown]).size / 1024) : 0;

  const run = async (label: string, fn: () => Promise<void> | void) => {
    setBusy(label);
    try {
      await fn();
    } catch (e: any) {
      setToast("分享失败：" + String(e?.message ?? e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="modal-mask" onClick={onClose}>
      <div className="modal wide" onClick={(e) => e.stopPropagation()}>
        <h2>分享</h2>
        <div className="share-scope">
          <label>
            <input type="radio" name="share-scope" checked={scope === "chapter"}
                   onChange={() => setScope("chapter")} />
            <span>当前章节</span>
          </label>
          <label>
            <input type="radio" name="share-scope" checked={scope === "book"}
                   onChange={() => setScope("book")} />
            <span>整本教材</span>
          </label>
        </div>
        {err && <p className="share-note">加载失败：{err}</p>}
        {!data && !err && <p className="share-note">正在准备…</p>}
        {data && (
          <>
            <p className="share-note">
              {data.title} · {data.blocks.length} 个知识点 · Markdown {sizeKb} KB
              {Object.keys(data.figures).length
                ? " · 含 " + Object.keys(data.figures).length + " 张插图（HTML 内嵌）"
                : ""}
            </p>
            <textarea className="share-preview" readOnly
                      value={data.markdown.slice(0, 2000)} />
            <div className="share-actions">
              <button
                className="primary"
                disabled={busy !== null}
                title="复制 Markdown 文本，可粘到 Notion / Obsidian / Typora（公式会渲染）"
                onClick={() => void run("md", async () => {
                  await copyText(data.markdown);
                  setToast("已复制 Markdown（" + data.blocks.length + " 个知识点）");
                })}>
                复制 Markdown
              </button>
              <button
                disabled={busy !== null}
                title="下载 .md 文件"
                onClick={() => void run("mdl", () => {
                  downloadText(base + ".md", data.markdown, "text/markdown");
                  setToast("已下载 " + base + ".md");
                })}>
                下载 .md
              </button>
              <button
                disabled={busy !== null}
                title="下载单文件 HTML：公式字体与插图已内嵌，对方断网双击也能看"
                onClick={() => void run("html", async () => {
                  const html = await buildShareHtml(data);
                  downloadText(base + ".html", html, "text/html");
                  setToast("已下载 " + base + ".html（可离线打开）");
                })}>
                {busy === "html" ? "打包中…" : "下载单文件 HTML"}
              </button>
              <button
                disabled={busy !== null}
                title="复制纯文本，适合直接粘进聊天窗口（公式保留 TeX 源码）"
                onClick={() => void run("txt", async () => {
                  await copyText(buildPlainText(data));
                  setToast("已复制纯文本");
                })}>
                复制纯文本
              </button>
            </div>
            <p className="share-note">
              分享内容完全在本机生成，不经过任何服务器；HTML 里的公式与插图都随文件带走。
            </p>
          </>
        )}
        <div className="modal-actions">
          <button onClick={onClose}>关闭</button>
        </div>
      </div>
    </div>
  );
}
