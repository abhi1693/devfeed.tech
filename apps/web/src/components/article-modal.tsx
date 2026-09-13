"use client";
import { useEffect, useRef, useState, useTransition } from "react";
import { usePathname, useRouter } from "next/navigation";
import { ChevronLeft, ChevronRight, X } from "lucide-react";
import { animateReader } from "@/lib/reader-motion";
import { useArticleNavigation } from "./article-navigation";

export function ArticleModal({
  children,
  direct = false,
  slug,
  canonical,
}: {
  children: React.ReactNode;
  direct?: boolean;
  slug?: string;
  canonical?: string;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const operation = useRef(0);
  const closing = useRef(false);
  const exitAnimation = useRef<Animation | null>(null);
  const motionDirection = useRef<-1 | 1 | null>(null);
  const router = useRouter();
  const pathname = usePathname();
  const { motionRef, sequence, directEntry, setDirectEntry, hideDirect, setHideDirect } =
    useArticleNavigation();
  const [pending, startTransition] = useTransition();
  const [waiting, setWaiting] = useState(false);
  const active = !direct || (!hideDirect && (!slug || pathname === `/articles/${slug}`));
  const current = pathname?.split("/").pop();
  const index = sequence?.slugs.indexOf(current ?? "") ?? -1;
  const previous = index > 0 ? sequence?.slugs[index - 1] : undefined;
  const next = index >= 0 ? sequence?.slugs[index + 1] : undefined;
  const canLoad = index >= 0 && !!sequence?.hasMore;
  const busy = pending || waiting || !!sequence?.loading;
  function dismiss() {
    if (closing.current) return;
    closing.current = true;
    motionRef.current = null;
    const token = ++operation.current;
    const finish = () => {
      if (token !== operation.current) return;
      if (direct || directEntry) router.replace("/");
      else router.back();
    };
    const animation = animateReader(
      dialog.current,
      [
        { opacity: 1, transform: "scale(1)" },
        { opacity: 0, transform: "scale(.98)" },
      ],
      { duration: 160, fill: "forwards" },
    );
    exitAnimation.current = animation;
    if (animation) {
      dialog.current!.dataset.closing = "true";
      void animation.finished.then(finish, () => {});
    } else finish();
  }
  function navigate(target: string) {
    if (motionDirection.current)
      motionRef.current = { target, direction: motionDirection.current, at: Date.now() };
    startTransition(() => router.replace(`/articles/${target}`, { scroll: false }));
  }
  function move(direction: -1 | 1) {
    if (busy || closing.current) return;
    motionDirection.current = direction;
    const target = direction < 0 ? previous : next;
    if (target) navigate(target);
    else if (direction > 0 && canLoad) {
      setWaiting(true);
      const token = ++operation.current;
      void sequence!
        .loadMore()
        .then((target) => {
          if (target && token === operation.current && dialog.current?.open) navigate(target);
        })
        .finally(() => setWaiting(false));
    }
  }
  useEffect(() => {
    if (!active || !canonical) return;
    // The retained feed owns Next's metadata during intercepted navigation.
    // Temporarily update its canonical while the article URL is active.
    const existing = document.head.querySelector<HTMLLinkElement>('link[rel="canonical"]');
    const link = existing ?? document.createElement("link");
    const previous = existing?.getAttribute("href");
    link.rel = "canonical";
    link.href = canonical;
    if (!existing) document.head.append(link);
    return () => {
      if (link.getAttribute("href") !== canonical) return;
      if (previous !== undefined && previous !== null) link.setAttribute("href", previous);
      else link.remove();
    };
  }, [active, canonical]);
  useEffect(() => {
    if (!direct) return;
    setDirectEntry(true);
    return () => {
      setDirectEntry(false);
      setHideDirect(false);
    };
  }, [direct, setDirectEntry, setHideDirect]);
  useEffect(() => {
    if (!direct && directEntry) setHideDirect(true);
  }, [direct, directEntry, setHideDirect]);
  useEffect(() => {
    if (!active) return;
    const element = dialog.current!;
    const previousFocus = document.activeElement as HTMLElement | null;
    const overflow = document.body.style.overflow;
    closing.current = false;
    delete element.dataset.closing;
    element.showModal();
    const entrance = animateReader(
      element,
      [
        { opacity: 0, transform: "scale(.98)" },
        { opacity: 1, transform: "scale(1)" },
      ],
      { duration: 200 },
    );
    document.body.style.overflow = "hidden";
    return () => {
      // Invalidate asynchronous page loads and dismissal callbacks, not a DOM ref.
      // eslint-disable-next-line react-hooks/exhaustive-deps
      operation.current++;
      entrance?.cancel();
      exitAnimation.current?.cancel();
      element.close();
      document.body.style.overflow = overflow;
      previousFocus?.focus({ preventScroll: true });
    };
  }, [active]);
  useEffect(() => {
    if (!active) return;
    dialog.current?.querySelector(".preview-scroll")?.scrollTo({ top: 0 });
    const intent = motionRef.current;
    if (!intent || intent.target !== pathname?.split("/").pop() || Date.now() - intent.at > 5000)
      return;
    const direction = intent.direction;
    motionRef.current = null;
    const animation = animateReader(
      dialog.current?.querySelector(".modal-content") ?? null,
      [
        { opacity: 0, transform: `translateX(${direction * 16}px)` },
        { opacity: 1, transform: "translateX(0)" },
      ],
      { duration: 180 },
    );
    return () => animation?.cancel();
  }, [active, pathname, motionRef]);
  if (!active) return null;
  return (
    <dialog
      ref={dialog}
      className="article-modal"
      aria-label="Article preview"
      aria-busy={busy}
      onCancel={(event) => {
        event.preventDefault();
        dismiss();
      }}
      onKeyDown={(event) => {
        if (
          event.defaultPrevented ||
          event.altKey ||
          event.ctrlKey ||
          event.metaKey ||
          event.shiftKey ||
          event.repeat
        )
          return;
        const target = event.target as HTMLElement;
        if (
          target.closest(
            'input, textarea, select, [contenteditable="true"], [role="combobox"], [role="listbox"], [role="menu"], [role="slider"]',
          )
        )
          return;
        if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
          event.preventDefault();
          move(event.key === "ArrowLeft" ? -1 : 1);
        }
      }}
      onClick={(event) => {
        if (event.target !== event.currentTarget) return;
        const bounds = event.currentTarget.getBoundingClientRect();
        if (
          event.clientX < bounds.left ||
          event.clientX > bounds.right ||
          event.clientY < bounds.top ||
          event.clientY > bounds.bottom
        )
          dismiss();
      }}
    >
      <header className="modal-toolbar">
        <nav aria-label="Article navigation">
          <button
            type="button"
            aria-label="Previous article"
            aria-keyshortcuts="ArrowLeft"
            title="Previous article (←)"
            disabled={busy || !previous}
            onClick={() => move(-1)}
          >
            <ChevronLeft size={18} aria-hidden />
          </button>
          <button
            type="button"
            aria-label="Next article"
            aria-keyshortcuts="ArrowRight"
            title="Next article (→)"
            disabled={busy || (!next && !canLoad)}
            onClick={() => move(1)}
          >
            <ChevronRight size={18} aria-hidden />
          </button>
        </nav>
        <button
          type="button"
          aria-label="Close preview"
          title="Close preview (Esc)"
          onClick={dismiss}
        >
          <X size={18} aria-hidden />
        </button>
      </header>
      <div className="modal-content">{children}</div>
    </dialog>
  );
}
