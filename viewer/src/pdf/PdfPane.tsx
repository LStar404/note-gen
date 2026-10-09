import { useEffect, useMemo, useRef, useState } from "react";
import * as pdfjs from "pdfjs-dist";
import type { ReactElement } from "react";
import { api } from "../api/client";
import { pdfRectToScreen, screenRectToPdf, type Rect, type ScreenRect } from "./coord";
import { useStore } from "../state/store";

pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.mjs",
  import.meta.url
).toString();

function token(): string {
  const meta = document.querySelector('meta[name="local-token"]');
  return meta ? meta.getAttribute("content") || "" : "";
}

const RELATION_COLORS: Record<string, string> = {
  defines: "rgba(59,130,246,0.28)",
  states: "rgba(16,185,129,0.28)",
  proves: "rgba(168,85,247,0.28)",
  motivates: "rgba(245,158,11,0.28)",
  illustrates: "rgba(236,72,153,0.28)",
  summarizes: "rgba(14,165,233,0.28)",
};

interface HighlightRect extends ScreenRect {
  color: string;
  pulse: boolean;
}

export default function PdfPane(): ReactElement {
  const docId = useStore((s) => s.docId);
  const pdfPage = useStore((s) => s.pdfPage);
  const setPdfPage = useStore((s) => s.setPdfPage);
  const highlight = useStore((s) => s.highlightAnchor);
  const selectionHits = useStore((s) => s.selectionHits);
  const setSelectionHits = useStore((s) => s.setSelectionHits);
  const setToast = useStore((s) => s.setToast);

  const canvasRef = useRef<HTMLCanvasElement>(null);
  const pageRef = useRef<HTMLDivElement>(null);
  const docRef = useRef<any>(null);
  const viewportRef = useRef<any>(null);
  const [numPages, setNumPages] = useState(0);
  const [scale, setScale] = useState(1.2);
  const [textItems, setTextItems] = useState<any[]>([]);
  const [pageLabels, setPageLabels] = useState<string[] | null>(null);

  useEffect(() => {
    if (!docId) return;
    let cancelled = false;
    (async () => {
      const task = pdfjs.getDocument({
        url: api.pdfUrl(docId),
        httpHeaders: { "X-Local-Token": token() },
      });
      const doc = await task.promise;
      if (cancelled) return;
      docRef.current = doc;
      setNumPages(doc.numPages);
      try {
        const labels = await doc.getPageLabels();
        setPageLabels(labels ?? null);
      } catch {
        setPageLabels(null);
      }
      setPdfPage(1);
    })().catch((e: Error) => setToast("PDF 加载失败：" + e.message));
    return () => {
      cancelled = true;
    };
  }, [docId]);

  useEffect(() => {
    const doc = docRef.current;
    if (!doc || !canvasRef.current) return;
    let cancelled = false;
    (async () => {
      const page = await doc.getPage(pdfPage);
      if (cancelled) return;
      const viewport = page.getViewport({ scale });
      viewportRef.current = viewport;
      const canvas = canvasRef.current!;
      const ctx = canvas.getContext("2d")!;
      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.floor(viewport.width * dpr);
      canvas.height = Math.floor(viewport.height * dpr);
      canvas.style.width = viewport.width + "px";
      canvas.style.height = viewport.height + "px";
      await page.render({
        canvasContext: ctx,
        viewport,
        transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : undefined,
      }).promise;
      const content = await page.getTextContent();
      if (cancelled) return;
      setTextItems(content.items);
    })();
    return () => {
      cancelled = true;
    };
  }, [docId, pdfPage, scale]);

  // 翻页联动（7.3）：预取前后各 1 页
  useEffect(() => {
    if (!docId || !numPages) return;
    [pdfPage - 1, pdfPage + 1].forEach((p) => {
      if (p >= 1 && p <= numPages) {
        void api.pageBlocks(docId, p).catch(() => undefined);
      }
    });
  }, [docId, pdfPage, numPages]);

  const highlights: HighlightRect[] = useMemo(() => {
    const viewport = viewportRef.current;
    const out: HighlightRect[] = [];
    if (!viewport) return out;
    const push = (bbox: Rect, color: string, pulse: boolean) => {
      out.push({ ...pdfRectToScreen(bbox, viewport), color, pulse });
    };
    if (highlight && highlight.page === pdfPage) {
      push(highlight.bbox, "rgba(59,130,246,0.35)", true);
    }
    for (const hit of selectionHits) {
      if (hit.page === pdfPage && (!highlight || hit.anchor_id !== highlight.anchor_id)) {
        push(hit.bbox, RELATION_COLORS[hit.match] ?? "rgba(245,158,11,0.3)", false);
      }
    }
    return out;
  }, [highlight, selectionHits, pdfPage, scale, textItems]);

  const onMouseUp = () => {
    const viewport = viewportRef.current;
    const container = pageRef.current;
    if (!viewport || !container || !docId) return;
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed || sel.rangeCount === 0) return;
    const containerRect = container.getBoundingClientRect();
    const rects: Rect[] = [];
    for (const raw of Array.from(sel.getRangeAt(0).getClientRects())) {
      if (raw.width <= 0 || raw.height <= 0) continue;
      rects.push([
        raw.left - containerRect.left,
        raw.top - containerRect.top,
        raw.right - containerRect.left,
        raw.bottom - containerRect.top,
      ]);
    }
    if (!rects.length) return;
    const pdfRects = rects.map((r) => screenRectToPdf(r, viewport));
    const text = sel.toString();
    api
      .match(docId, { page: pdfPage, rects: pdfRects, text })
      .then((res) => setSelectionHits(res.hits))
      .catch((e: Error) => setToast("选区匹配失败：" + e.message));
  };

  return (
    <div className="pdf-pane">
      <div className="pdf-toolbar">
        <button onClick={() => setPdfPage(Math.max(1, pdfPage - 1))} disabled={pdfPage <= 1}>
          上一页
        </button>
        <span className="page-indicator">
          {pdfPage} / {numPages || "?"}
          {pageLabels && pageLabels[pdfPage - 1] ? "（印刷页 " + pageLabels[pdfPage - 1] + "）" : ""}
        </span>
        <button onClick={() => setPdfPage(Math.min(numPages, pdfPage + 1))} disabled={pdfPage >= numPages}>
          下一页
        </button>
        <button onClick={() => setScale((s) => Math.max(0.5, s - 0.2))}>－</button>
        <button onClick={() => setScale((s) => Math.min(3, s + 0.2))}>＋</button>
      </div>
      <div className="pdf-scroll">
        <div className="pdf-page" ref={pageRef} onMouseUp={onMouseUp}>
          <canvas ref={canvasRef} />
          <div className="text-layer">
            {textItems.map((item, i) => {
              const viewport = viewportRef.current;
              if (!viewport || !item.str) return null;
              const tx = pdfjs.Util.transform(viewport.transform, item.transform);
              const fontSize = Math.hypot(tx[2], tx[3]);
              return (
                <span
                  key={i}
                  style={{
                    left: tx[4] + "px",
                    top: tx[5] - fontSize + "px",
                    fontSize: fontSize + "px",
                    height: fontSize + "px",
                  }}
                >
                  {item.str}
                </span>
              );
            })}
          </div>
          <div className="highlight-layer">
            {highlights.map((h, i) => (
              <div
                key={i}
                className={h.pulse ? "highlight pulse" : "highlight"}
                style={{
                  left: h.left + "px",
                  top: h.top + "px",
                  width: h.width + "px",
                  height: h.height + "px",
                  background: h.color,
                }}
              />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
