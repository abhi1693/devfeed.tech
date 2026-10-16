import "server-only";
import type { GatewayCookiePolicy } from "@devfeed/http/gateway";

const readerCookies = new Set([
  "devfeed_user_session",
  "__Host-devfeed_user_session",
  "devfeed_user_state",
  "__Host-devfeed_user_state",
  "devfeed_user_visitor",
  "__Host-devfeed_user_visitor",
]);
const attributionCookies = new Set([
  "devfeed_user_x_click",
  "__Host-devfeed_user_x_click",
  "_twclid",
]);

/** Forward only the cookie families declared by the selected reader operation. */
export class ReaderCookies {
  constructor(private readonly attributionEnabled: () => boolean) {}

  filter(value: string, policy: GatewayCookiePolicy): string {
    if (policy === "omit") return "";
    const attribution = policy === "reader-attribution" && this.attributionEnabled();
    return value
      .split(";")
      .map((part) => part.trim())
      .filter((part) => {
        const separator = part.indexOf("=");
        if (separator < 1) return false;
        const name = part.slice(0, separator);
        return readerCookies.has(name) || (attribution && attributionCookies.has(name));
      })
      .join("; ");
  }
}
