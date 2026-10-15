import { readerRequest } from "./reader-runtime";
import { partnerReceiptExpiresAt } from "./partner-receipts";

export type PartnerPlacementTrackingState = {
  completed: Set<string>;
  pending: Set<string>;
};

/** Bind a placement's clickable element using its server-issued delivery receipt. */
export function observePartnerPlacement(
  element: HTMLElement,
  receipt: string,
  state?: PartnerPlacementTrackingState,
  clickSelector?: string,
) {
  const expiresAt = partnerReceiptExpiresAt(receipt);
  if (expiresAt <= Date.now()) return () => {};
  const { completed, pending } = state ?? {
    completed: new Set<string>(),
    pending: new Set<string>(),
  };
  let inView = false;
  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | undefined;

  function current() {
    return !stopped && Date.now() < expiresAt;
  }

  async function report(kind: "impression" | "click") {
    if (!current() || completed.has(kind) || pending.has(kind)) return;
    pending.add(kind);
    try {
      for (let attempt = 0; attempt < 2; attempt++) {
        // Opening an article preview can detach this observer immediately after a real
        // click. Finish that accepted event without allowing new events after cleanup.
        if (Date.now() >= expiresAt || (kind === "impression" && stopped)) return;
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
    if (!current() || !inView || document.visibilityState !== "visible") return;
    if (completed.has("impression") || pending.has("impression")) return;
    timer = setTimeout(() => {
      timer = undefined;
      if (current() && element.isConnected && document.visibilityState === "visible") {
        void report("impression");
      }
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
    if (
      !event.isTrusted ||
      (event.type === "click" ? event.button !== 0 : event.button !== 1) ||
      document.visibilityState !== "visible" ||
      !element.isConnected ||
      element.closest("[inert], [hidden], [aria-hidden='true']") ||
      !(event.target instanceof Element)
    )
      return;
    const destination = event.target.closest("a[href]");
    if (
      !destination ||
      !element.contains(destination) ||
      destination.closest("[inert], [hidden], [aria-hidden='true']") ||
      (clickSelector && !destination.matches(clickSelector))
    )
      return;
    // A trusted keyboard activation is a primary click too. Internal article links may
    // prevent the browser's default navigation while opening their reader preview.
    void report("click");
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
