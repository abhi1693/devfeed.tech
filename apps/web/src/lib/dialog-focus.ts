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
