// Next serves both products from one image. The document carries the server's
// runtime choice into client bundles, including client-side navigations.
export function isHardwareDaily() {
  const product =
    typeof document === "undefined" ? undefined : document.documentElement.dataset.product;
  return (
    (product ?? (typeof process === "undefined" ? undefined : process.env.DEVFEED_PRODUCT)) ===
    "hardwaredaily"
  );
}

export function productName() {
  return isHardwareDaily() ? "Hardware Daily" : "DevFeed";
}

export function productWordmark() {
  return isHardwareDaily() ? "Hardware Daily" : "devfeed.";
}
