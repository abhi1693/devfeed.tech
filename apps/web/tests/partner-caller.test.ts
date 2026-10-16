import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { partnerCallerHeaders } from "@/lib/server/partner-caller";

const key = "disposable-partner-caller-key-32-bytes";
const placementPath = "/v1/partner-placements/query";
const trackingPath = "/v1/partner-tracking/events";

beforeEach(() => {
  vi.stubEnv("DEVFEED_PARTNER_CALLER_IP_HEADER", "cf-connecting-ip");
  vi.stubEnv("DEVFEED_PARTNER_CALLER_KEY", key);
  vi.spyOn(Date, "now").mockReturnValue(1_800_000_000_999);
});
afterEach(() => vi.unstubAllEnvs());

function request(headers: Record<string, string> = {}) {
  return new Request("https://devfeed.tech/api/v1/partner-placements/query", {
    method: "POST",
    headers,
  });
}

it.each([undefined, "", " "])(
  "ignores all caller and forwarding claims when the trusted header is unconfigured (%s)",
  (ipHeader) => {
    vi.stubEnv("DEVFEED_PARTNER_CALLER_IP_HEADER", ipHeader);
    vi.stubEnv("DEVFEED_PARTNER_CALLER_KEY", undefined);
    expect(
      partnerCallerHeaders(
        request({
          "cf-connecting-ip": "203.0.113.7",
          "X-DevFeed-Partner-Caller": "forged-proof",
          "X-Forwarded-For": "198.51.100.2",
          Forwarded: 'for="198.51.100.3"',
        }),
        placementPath,
      ),
    ).toEqual({});
  },
);

it.each([
  [placementPath, "cebe4cf3062024e2b0a188fb0dc46d40e61d42cbb52e21805fca33c2d8d79ada"],
  [trackingPath, "261cbf16c4ee77a0e2a10c0d344a1190b143e1ba4c588e673b95cb2aa9735218"],
])("signs the explicit edge address for the fixed POST destination %s", (path, signature) => {
  expect(
    partnerCallerHeaders(
      request({
        "cf-connecting-ip": "203.0.113.7",
        "X-DevFeed-Partner-Caller": "forged-proof",
        "X-Forwarded-For": "198.51.100.2",
      }),
      path,
    ),
  ).toEqual({ "X-DevFeed-Partner-Caller": `v1.1800000000.203.0.113.7.${signature}` });
});

it.each(["2001:db8::7", "::ffff:203.0.113.7"])("accepts a single IPv6 address (%s)", (ip) => {
  expect(partnerCallerHeaders(request({ "cf-connecting-ip": ip }), placementPath)).toEqual({
    "X-DevFeed-Partner-Caller": expect.stringMatching(
      new RegExp(`^v1\\.1800000000\\.${ip.replaceAll(".", "\\.")}\\.[a-f0-9]{64}$`),
    ),
  });
});

it.each([undefined, "", " ".repeat(32), "too-short"])(
  "rejects configured trust without a valid key (%s)",
  (value) => {
    vi.stubEnv("DEVFEED_PARTNER_CALLER_KEY", value);
    expect(() =>
      partnerCallerHeaders(request({ "cf-connecting-ip": "203.0.113.7" }), placementPath),
    ).toThrow("DEVFEED_PARTNER_CALLER_KEY");
  },
);

it.each([
  "",
  "not-an-address",
  "203.0.113.7, 198.51.100.1",
  "203.0.113.7:443",
  "[2001:db8::7]",
  "fe80::1%eth0",
])("rejects invalid or ambiguous edge addresses (%s)", (ip) => {
  expect(() => partnerCallerHeaders(request({ "cf-connecting-ip": ip }), placementPath)).toThrow(
    "one IP address",
  );
});

it("does not replace a missing configured edge address with browser forwarding headers", () => {
  expect(() =>
    partnerCallerHeaders(
      request({ "X-Forwarded-For": "203.0.113.7", "X-DevFeed-Partner-Caller": "forged-proof" }),
      placementPath,
    ),
  ).toThrow("one IP address");
});
