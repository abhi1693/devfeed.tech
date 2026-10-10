/** Keep sequential keyboard focus inside an open modal at either boundary. */
export function cycleDialogFocus(dialog: HTMLDialogElement, backwards: boolean): boolean {
  const controls = Array.from(
    dialog.querySelectorAll<HTMLElement>("a[href], button, input, select, textarea, [tabindex]"),
  ).filter(
    (node) =>
      node.tabIndex >= 0 &&
      !node.matches(":disabled") &&
      !node.closest("[inert]") &&
      node.getClientRects().length > 0 &&
      getComputedStyle(node).visibility !== "hidden",
  );
  const first = controls[0];
  const last = controls.at(-1);
  if (!first || !last) return false;
  const boundary = backwards ? first : last;
  if (document.activeElement !== boundary) return false;
  (backwards ? last : first).focus();
  return true;
}

/** A history session check can temporarily hide the retained preview trigger. */
export function restoreDialogFocus(target: HTMLElement) {
  if (!target.isConnected) return;
  target.focus({ preventScroll: true });
  if (document.activeElement === target) return;
  let frame = 0;
  const deadline = performance.now() + 3000;
  const stop = () => {
    cancelAnimationFrame(frame);
    window.removeEventListener("pointerdown", stop);
    window.removeEventListener("keydown", stop);
  };
  const attempt = () => {
    if (!target.isConnected || performance.now() >= deadline) {
      stop();
      return;
    }
    target.focus({ preventScroll: true });
    if (document.activeElement === target) stop();
    else frame = requestAnimationFrame(attempt);
  };
  window.addEventListener("pointerdown", stop);
  window.addEventListener("keydown", stop);
  frame = requestAnimationFrame(attempt);
}
