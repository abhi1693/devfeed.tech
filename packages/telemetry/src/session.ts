/** Empty means optional telemetry cannot obtain a cryptographically random session ID. */
export function generateSessionId(): string {
  try {
    const cryptoApi = globalThis.crypto;
    try {
      if (typeof cryptoApi?.randomUUID === "function") return cryptoApi.randomUUID();
    } catch {
      // UUID generation may be unavailable while secure random bytes still work.
    }
    if (typeof cryptoApi?.getRandomValues === "function") {
      const bytes = cryptoApi.getRandomValues(new Uint8Array(16));
      bytes[6] = (bytes[6] & 0x0f) | 0x40;
      bytes[8] = (bytes[8] & 0x3f) | 0x80;
      const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
      return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
    }
  } catch {
    // Restricted or unavailable crypto must disable telemetry rather than break the application.
  }

  return "";
}
