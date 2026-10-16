// Store coordinates on the browser entry, never feed/account/profile data.
export function saveHistoryScroll() {
  const state = window.history.state;
  const position = state?.readerScroll;
  const { scrollX: x, scrollY: y } = window;
  if (Array.isArray(position) && position.length === 2 && position[0] === x && position[1] === y)
    return;
  try {
    window.history.replaceState({ ...state, readerScroll: [x, y] }, "");
  } catch (error) {
    if (
      !(error instanceof DOMException) ||
      !["SecurityError", "QuotaExceededError"].includes(error.name)
    )
      throw error;
  }
}

/** Reader routes and account checks may render after native history restoration. */
export function startHistoryScroll() {
  const previous = history.scrollRestoration;
  history.scrollRestoration = "manual";
  let frame = 0;
  let restoring = false;
  let saveTimer: ReturnType<typeof setTimeout> | undefined;
  const cancelSave = () => {
    clearTimeout(saveTimer);
    saveTimer = undefined;
  };
  const stop = () => {
    cancelAnimationFrame(frame);
    restoring = false;
  };
  const save = () => {
    cancelSave();
    if (!restoring) saveHistoryScroll();
  };
  const scheduleSave = () => {
    if (!restoring && saveTimer === undefined) saveTimer = setTimeout(save, 500);
  };
  const navigate = () => {
    save();
    stop();
  };
  const extensionRoute = () => {
    cancelSave();
    stop();
  };
  const restore = () => {
    cancelSave();
    stop();
    const position: unknown = history.state?.readerScroll;
    if (
      !Array.isArray(position) ||
      position.length !== 2 ||
      !position.every((value) => typeof value === "number" && Number.isFinite(value) && value >= 0)
    )
      return;
    const [x, y] = position;
    const deadline = performance.now() + 3000;
    restoring = true;
    const attempt = () => {
      window.scrollTo({ left: x, top: y, behavior: "instant" });
      if (
        (Math.abs(scrollX - x) < 1 && Math.abs(scrollY - y) < 1) ||
        performance.now() >= deadline
      ) {
        stop();
        return;
      }
      frame = requestAnimationFrame(attempt);
    };
    // Wait for React to commit the destination rather than scrolling the old route.
    frame = requestAnimationFrame(() => {
      frame = requestAnimationFrame(attempt);
    });
  };
  const navigation = performance.getEntriesByType?.("navigation")[0] as
    PerformanceNavigationTiming | undefined;
  if (navigation?.type === "back_forward" || navigation?.type === "reload") restore();
  else saveHistoryScroll();
  window.addEventListener("scroll", scheduleSave, { passive: true });
  window.addEventListener("pagehide", save);
  window.addEventListener("popstate", restore);
  window.addEventListener("devfeed:extension-route", extensionRoute);
  window.addEventListener("devfeed:reader-navigation", navigate);
  window.addEventListener("pointerdown", stop);
  window.addEventListener("wheel", stop, { passive: true });
  window.addEventListener("touchstart", stop, { passive: true });
  window.addEventListener("keydown", stop);
  return () => {
    cancelSave();
    stop();
    history.scrollRestoration = previous;
    window.removeEventListener("scroll", scheduleSave);
    window.removeEventListener("pagehide", save);
    window.removeEventListener("popstate", restore);
    window.removeEventListener("devfeed:extension-route", extensionRoute);
    window.removeEventListener("devfeed:reader-navigation", navigate);
    window.removeEventListener("pointerdown", stop);
    window.removeEventListener("wheel", stop);
    window.removeEventListener("touchstart", stop);
    window.removeEventListener("keydown", stop);
  };
}
