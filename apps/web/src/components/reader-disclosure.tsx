"use client";
import { useEffect, useRef, type ReactNode } from "react";
import { animateReader } from "@/lib/reader-motion";

export function ReaderDisclosure({
  title,
  children,
  className,
  popover = false,
}: {
  title: ReactNode;
  children: ReactNode;
  className?: string;
  popover?: boolean;
}) {
  const details = useRef<HTMLDetailsElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const animation = useRef<Animation | null>(null);
  const expanded = useRef(false);
  useEffect(
    () => () => {
      animation.current?.cancel();
      animation.current = null;
    },
    [],
  );
  return (
    <details ref={details} className={className}>
      <summary
        onClick={(event) => {
          event.preventDefault();
          const element = details.current!;
          const body = content.current!;
          const height = element.open ? body.getBoundingClientRect().height : 0;
          animation.current?.cancel();
          expanded.current = !expanded.current;
          element.open = true;
          const open = expanded.current;
          const effect = animateReader(
            popover ? body.firstElementChild : body,
            popover
              ? [
                  {
                    opacity: open ? 0 : 1,
                    transform: open ? "scale(.97) translateY(-3px)" : "scale(1)",
                  },
                  {
                    opacity: open ? 1 : 0,
                    transform: open ? "scale(1)" : "scale(.97) translateY(-3px)",
                  },
                ]
              : [
                  { height: `${height}px`, opacity: open ? 0 : 1 },
                  { height: `${open ? body.scrollHeight : 0}px`, opacity: open ? 1 : 0 },
                ],
            { duration: 200 },
          );
          animation.current = effect;
          if (!effect) element.open = open;
          else
            void effect.finished.then(
              () => {
                if (animation.current !== effect) return;
                element.open = open;
                animation.current = null;
              },
              () => {},
            );
        }}
      >
        {title}
      </summary>
      <div ref={content} style={{ overflow: popover ? "visible" : "hidden" }}>
        {children}
      </div>
    </details>
  );
}
