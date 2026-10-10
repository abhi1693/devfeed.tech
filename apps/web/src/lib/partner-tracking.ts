import { readerRequest } from "./reader-runtime";

/** Bind to a future placement's clickable element, using its server-issued delivery receipt. */
export function observePartnerPlacement(element: HTMLElement, receipt: string) {
  if (!receipt || receipt.length > 256) return () => {};
  const completed = new Set<string>();
  const pending = new Set<string>();
  let inView = false;
  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | undefined;

  async function report(kind: "impression" | "click") {
    if (completed.has(kind) || pending.has(kind)) return;
    pending.add(kind);
    try {
      for (let attempt = 0; attempt < 2; attempt++) {
        try {
          const response = await readerRequest("/api/v1/partner-tracking/events", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ kind, receipt }),
            cache: "no-store",
            credentials: "omit",
            referrerPolicy: "no-referrer",
            keepalive: true,
          });
          if (response.ok) {
            completed.add(kind);
            return;
          }
          if (response.status < 500 && response.status !== 429) return;
        } catch {
          // Tracking must never prevent a placement link from navigating.
        }
        if (attempt === 0) await new Promise((resolve) => setTimeout(resolve, 1000));
      }
    } finally {
      pending.delete(kind);
    }
  }

  function visibility() {
    if (timer !== undefined) clearTimeout(timer);
    timer = undefined;
    if (stopped || !inView || document.visibilityState !== "visible") return;
    if (completed.has("impression") || pending.has("impression")) return;
    timer = setTimeout(() => {
      timer = undefined;
      if (element.isConnected && document.visibilityState === "visible") void report("impression");
    }, 1000);
  }

  const observer =
    typeof IntersectionObserver === "undefined"
      ? undefined
      : new IntersectionObserver(
          (entries) => {
            const entry = entries.find((entry) => entry.target === element);
            if (!entry) return;
            inView = entry.isIntersecting && entry.intersectionRatio >= 0.5;
            visibility();
          },
          { threshold: [0, 0.5] },
        );
  function click(event: MouseEvent) {
    if (event.type === "click" || event.button === 1) void report("click");
  }
  observer?.observe(element);
  element.addEventListener("click", click);
  element.addEventListener("auxclick", click);
  document.addEventListener("visibilitychange", visibility);
  return () => {
    stopped = true;
    if (timer !== undefined) clearTimeout(timer);
    observer?.disconnect();
    element.removeEventListener("click", click);
    element.removeEventListener("auxclick", click);
    document.removeEventListener("visibilitychange", visibility);
  };
}
