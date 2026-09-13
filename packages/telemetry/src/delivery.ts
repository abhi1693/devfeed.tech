// Shared across Next's server bundles without exporting any browser state.
type DeliveryGlobals = typeof globalThis & { devfeedFaroDelivery?: (status: number) => void };
export function recordDelivery(status: number) {
  (globalThis as DeliveryGlobals).devfeedFaroDelivery?.(status);
}
export function observeDeliveries(callback: (status: number) => void) {
  (globalThis as DeliveryGlobals).devfeedFaroDelivery = callback;
}
