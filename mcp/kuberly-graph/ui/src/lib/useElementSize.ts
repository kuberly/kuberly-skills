import { useEffect, useRef, useState } from "react";

// Tracks {width, height} of a DOM element via ResizeObserver. Returns
// 0/0 until the element has been laid out at least once. Uses
// `getBoundingClientRect` to read sub-pixel-correct sizes (matters for
// the WebGL viewport).
export function useElementSize<T extends HTMLElement>() {
  const ref = useRef<T | null>(null);
  const [size, setSize] = useState<{ width: number; height: number }>({ width: 0, height: 0 });

  useEffect(() => {
    const el = ref.current;
    if (!el) return;

    const update = () => {
      const r = el.getBoundingClientRect();
      setSize({ width: Math.round(r.width), height: Math.round(r.height) });
    };
    update();

    const ro = new ResizeObserver(() => update());
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  return [ref, size] as const;
}
