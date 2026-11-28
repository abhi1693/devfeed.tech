export function isHardwareDaily() {
  return process.env.DEVFEED_PRODUCT === "hardwaredaily";
}

export function readerName() {
  return isHardwareDaily() ? "Hardware Daily" : "DevFeed";
}

export const HARDWARE_DESCRIPTION =
  "Electronics and embedded engineering news, from firmware and circuit design to silicon and manufacturing.";
