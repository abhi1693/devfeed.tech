/** Browser work belongs to a visible, focused page. User mutations are not gated. */
export function isPageActive() {
  return document.visibilityState !== "hidden" && document.hasFocus();
}

/** Starts a fresh cancellable scope on each return; paired browser events coalesce. */
export function runWhenPageActive(start: (signal: AbortSignal, resumed: boolean) => void | (() => void)) {
  let focused = document.hasFocus();
  let active: boolean | undefined;
  let controller: AbortController | undefined;
  let cleanup: void | (() => void);
  function stop() {
    controller?.abort();
    controller = undefined;
    cleanup?.();
    cleanup = undefined;
  }
  function update() {
    const next = document.visibilityState !== "hidden" && focused;
    if (next === active) return;
    const resumed = active !== undefined;
    active = next;
    stop();
    if (next) {
      controller = new AbortController();
      cleanup = start(controller.signal, resumed);
    }
  }
  const visibility = () => { focused = document.hasFocus(); update(); };
  const focus = (event: Event) => { if (event.target === event.currentTarget) { focused = true; update(); } };
  const blur = (event: Event) => { if (event.target === event.currentTarget) { focused = false; update(); } };
  document.addEventListener("visibilitychange", visibility);
  window.addEventListener("focus", focus);
  window.addEventListener("blur", blur);
  update();
  return () => {
    document.removeEventListener("visibilitychange", visibility);
    window.removeEventListener("focus", focus);
    window.removeEventListener("blur", blur);
    stop();
  };
}
