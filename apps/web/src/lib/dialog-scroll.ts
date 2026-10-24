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
  // A slow route may commit after this correction. Keep its activation position
  // available until the modal claims it or later input supersedes navigation.
  const frame = requestAnimationFrame(restore);
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
  // arrive after the first correction frame. Retain the entry position for the
  // entire modal lifetime without writing again for our own correction event.
  const retainPosition = () => {
    if (window.scrollX !== position.left || window.scrollY !== position.top) restore();
  };
  window.addEventListener("scroll", retainPosition, { passive: true });
  const frame = requestAnimationFrame(restore);
  return () => {
    cancelAnimationFrame(frame);
    window.removeEventListener("scroll", retainPosition);
    document.body.style.overflow = overflow;
  };
}
