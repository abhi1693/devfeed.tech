"use client";
import { useEffect, useRef, useState, useTransition } from "react";
import { usePathname, useRouter } from "next/navigation";
import { ChevronLeft, ChevronRight, X } from "lucide-react";
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
  const router = useRouter();
  const pathname = usePathname();
  const { sequence, directEntry, setDirectEntry, hideDirect, setHideDirect } =
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
    operation.current++;
    if (direct || directEntry) router.replace("/");
    else router.back();
  }
  function navigate(target: string) {
    startTransition(() => router.replace(`/articles/${target}`, { scroll: false }));
  }
  function move(direction: -1 | 1) {
    if (busy) return;
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
    element.showModal();
    document.body.style.overflow = "hidden";
    return () => {
      element.close();
      document.body.style.overflow = overflow;
      previousFocus?.focus({ preventScroll: true });
    };
  }, [active]);
  useEffect(() => {
    if (active) dialog.current?.querySelector(".preview-scroll")?.scrollTo({ top: 0 });
  }, [active, pathname]);
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
