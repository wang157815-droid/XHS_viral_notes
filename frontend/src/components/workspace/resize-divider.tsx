"use client";

import { useCallback, useEffect, useRef } from "react";

interface ResizeDividerProps {
  /** 拖拽时传出横向增量 deltaX（正数 = 向右，负数 = 向左） */
  onDrag: (dx: number) => void;
}

/**
 * 竖向可拖拽分隔器，插入 Chat 与 Canvas 之间。
 * 纯原生 mousedown → mousemove → mouseup，无第三方依赖。
 */
export function ResizeDivider({ onDrag }: ResizeDividerProps) {
  const draggingRef = useRef(false);
  const lastXRef = useRef(0);

  const onMouseDown = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      draggingRef.current = true;
      lastXRef.current = e.clientX;
    },
    [],
  );

  useEffect(() => {
    const onMouseMove = (e: MouseEvent) => {
      if (!draggingRef.current) return;
      const dx = e.clientX - lastXRef.current;
      lastXRef.current = e.clientX;
      if (dx !== 0) onDrag(dx);
    };
    const onMouseUp = () => {
      draggingRef.current = false;
    };
    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mouseup", onMouseUp);
    return () => {
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup", onMouseUp);
    };
  }, [onDrag]);

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label="拖拽调整宽度"
      onMouseDown={onMouseDown}
      className="group relative z-10 flex w-[6px] shrink-0 cursor-col-resize select-none items-center justify-center border-x border-x-transparent hover:border-x-dew/30"
    >
      {/* 中间竖线指示器，hover/active 时加强 */}
      <div className="h-12 w-[2px] rounded-full bg-black/[0.07] transition-colors group-hover:bg-dew/50 group-active:bg-dew/70" />
    </div>
  );
}
