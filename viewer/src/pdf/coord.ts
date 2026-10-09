/** 坐标转换（规范 6.1 / 附录 D）：存储为 PDF 用户空间，前端只用 viewport 正/逆变换。 */

export type Rect = [number, number, number, number];

export interface PageViewportLike {
  convertToViewportRectangle(rect: number[]): number[];
  convertToPdfPoint(x: number, y: number): number[];
}

export interface ScreenRect {
  left: number;
  top: number;
  width: number;
  height: number;
}

/** PDF 用户空间矩形 → 当前 viewport 的 CSS 像素矩形（不含 DPR）。 */
export function pdfRectToScreen(rect: Rect, viewport: PageViewportLike): ScreenRect {
  const [x0, y0, x1, y1] = viewport.convertToViewportRectangle(rect);
  return {
    left: Math.min(x0, x1),
    top: Math.min(y0, y1),
    width: Math.abs(x1 - x0),
    height: Math.abs(y1 - y0),
  };
}

/** viewport CSS 像素矩形（已扣页容器偏移、还原到 CSS 尺度）→ PDF 用户空间矩形。 */
export function screenRectToPdf(rect: Rect, viewport: PageViewportLike): Rect {
  const [x0, y0, x1, y1] = rect;
  const points = [
    [x0, y0],
    [x0, y1],
    [x1, y0],
    [x1, y1],
  ].map(([x, y]) => viewport.convertToPdfPoint(x, y));
  const xs = points.map((p) => p[0]);
  const ys = points.map((p) => p[1]);
  return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
}
