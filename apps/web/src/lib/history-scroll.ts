// Store coordinates on the browser entry, never feed/account/profile data.
export function saveHistoryScroll() {
  window.history.replaceState(
    { ...window.history.state, readerScroll: [window.scrollX, window.scrollY] },
    "",
  );
}

/** Reader routes and account checks may render after native history restoration. */
export function startHistoryScroll() {
  const previous = history.scrollRestoration;
  history.scrollRestoration = "manual";
  let frame = 0;
  let restoring = false;
  const stop = () => {
    cancelAnimationFrame(frame);
    restoring = false;
  };
  const save = () => {
    if (!restoring) saveHistoryScroll();
  };
  const restore = () => {
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
  if (navigation?.type === "back_forward") restore();
  else saveHistoryScroll();
  window.addEventListener("scroll", save, { passive: true });
  window.addEventListener("pagehide", save);
  window.addEventListener("popstate", restore);
  window.addEventListener("devfeed:extension-route", stop);
  window.addEventListener("devfeed:reader-navigation", stop);
  window.addEventListener("pointerdown", stop);
  window.addEventListener("wheel", stop, { passive: true });
  window.addEventListener("touchstart", stop, { passive: true });
  window.addEventListener("keydown", stop);
  return () => {
    stop();
    history.scrollRestoration = previous;
    window.removeEventListener("scroll", save);
    window.removeEventListener("pagehide", save);
    window.removeEventListener("popstate", restore);
    window.removeEventListener("devfeed:extension-route", stop);
    window.removeEventListener("devfeed:reader-navigation", stop);
    window.removeEventListener("pointerdown", stop);
    window.removeEventListener("wheel", stop);
    window.removeEventListener("touchstart", stop);
    window.removeEventListener("keydown", stop);
  };
}
