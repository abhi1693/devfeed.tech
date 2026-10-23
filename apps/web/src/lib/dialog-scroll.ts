const interruptEvents = ["pointerdown", "wheel", "touchstart", "keydown", "popstate", "pagehide"];
let pending: { identity: string; restore: () => void; cancel: () => void } | undefined;

export function cancelDialogScrollPreparation() {
  pending?.cancel();
}

/** Stop native motion before an asynchronous preview route can render. */
export function prepareDialogScroll(identity: string) {
  cancelDialogScrollPreparation();
  const position = { left: window.scrollX, top: window.scrollY, behavior: "instant" as const };
  const restore = () => window.scrollTo(position);
  const cancel = () => {
    cancelAnimationFrame(frame);
    for (const event of interruptEvents) window.removeEventListener(event, cancel);
    pending = undefined;
  };
  const frame = requestAnimationFrame(() => {
    restore();
    cancel();
  });
  pending = { identity, restore, cancel };
  for (const event of interruptEvents) window.addEventListener(event, cancel, { passive: true });
  restore();
}

/** Freeze the document at modal entry, including an already-running browser scroll. */
export function lockDialogScroll(identity?: string) {
  // A route can commit between the queued compositor update and our next frame.
  // Adopt the activation coordinates before capturing the modal's scroll lock.
  if (pending && pending.identity === identity) pending.restore();
  cancelDialogScrollPreparation();
  const overflow = document.body.style.overflow;
  const position = { left: window.scrollX, top: window.scrollY, behavior: "instant" as const };
  const restore = () => window.scrollTo(position);
  document.body.style.overflow = "hidden";
  restore();
  // Overflow blocks new wheel input, but a queued compositor scroll can still
  // arrive once after cancellation. Correct it before the modal's next paint.
  const frame = requestAnimationFrame(restore);
  return () => {
    cancelAnimationFrame(frame);
    document.body.style.overflow = overflow;
  };
}
