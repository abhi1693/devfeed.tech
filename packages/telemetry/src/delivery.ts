// Shared across Next's server bundles without exporting any browser state.
export type DeliveryOutcome =
  | "accepted"
  | "disabled"
  | "collector_unconfigured"
  | "origin_unconfigured"
  | "origin_missing"
  | "origin_mismatch"
  | "content_encoding"
  | "unsupported_type"
  | "too_large"
  | "rate_limited"
  | "missing_body"
  | "body_timeout"
  | "invalid_body"
  | "upstream_rejected"
  | "upstream_error";
type DeliveryObserver = (status: number, outcome: DeliveryOutcome) => void;
type DeliveryGlobals = typeof globalThis & { devfeedFaroDelivery?: DeliveryObserver };
export function recordDelivery(status: number, outcome: DeliveryOutcome) {
  (globalThis as DeliveryGlobals).devfeedFaroDelivery?.(status, outcome);
}
export function observeDeliveries(callback: DeliveryObserver) {
  (globalThis as DeliveryGlobals).devfeedFaroDelivery = callback;
}
