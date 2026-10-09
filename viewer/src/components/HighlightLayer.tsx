/** 高亮层（规范 6.2）：relation 分色、2 秒脉冲、滚动居中。 */

import { useEffect, useRef } from "react";
import type { ScreenRect } from "../pdf/coord";

export interface HighlightRect extends ScreenRect {
  color: string;
  pulse: boolean;
  anchorId?: string;
}

export default function HighlightLayer({ rects, scrollTarget }: {
  rects: HighlightRect[];
  scrollTarget?: HighlightRect | null;
}) {
  const targetRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (scrollTarget && targetRef.current) {
      targetRef.current.scrollIntoView({ behavior: "smooth", block: "center", inline: "center" });
    }
  }, [scrollTarget]);

  return (
    <div className="highlight-layer">
      {rects.map((h, i) => (
        <div
          key={h.anchorId ?? i}
          ref={i === rects.length - 1 && scrollTarget ? targetRef : undefined}
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
  );
}
