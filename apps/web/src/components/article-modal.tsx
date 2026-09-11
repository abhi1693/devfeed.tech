"use client";
import { useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { X } from "lucide-react";

export function ArticleModal({ children }: { children: React.ReactNode }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const router = useRouter();
  useEffect(() => {
    const element = dialog.current!;
    const previous = document.activeElement as HTMLElement | null;
    const overflow = document.body.style.overflow;
    element.showModal();
    document.body.style.overflow = "hidden";
    return () => {
      element.close();
      document.body.style.overflow = overflow;
      previous?.focus({ preventScroll: true });
    };
  }, []);
  return (
    <dialog
      ref={dialog}
      className="article-modal"
      aria-label="Article preview"
      onCancel={(event) => {
        event.preventDefault();
        router.back();
      }}
      onClick={(event) => {
        if (event.target === event.currentTarget) {
          const bounds = event.currentTarget.getBoundingClientRect();
          if (
            event.clientX < bounds.left ||
            event.clientX > bounds.right ||
            event.clientY < bounds.top ||
            event.clientY > bounds.bottom
          )
            router.back();
        }
      }}
    >
      <div className="modal-toolbar">
        <span className="sr-only">Article preview</span>
        <button
          type="button"
          aria-label="Close preview"
          onClick={() => router.back()}
        >
          <X size={20} />
        </button>
      </div>
      <div className="modal-content">{children}</div>
    </dialog>
  );
}
