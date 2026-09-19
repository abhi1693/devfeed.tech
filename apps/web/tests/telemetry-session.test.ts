import { expect, it, vi } from "vitest";
import { generateSessionId } from "@devfeed/telemetry/session";

it("uses randomUUID when available", () => {
  vi.stubGlobal("crypto", { randomUUID: () => "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee" });
  expect(generateSessionId()).toBe("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee");
});

it("falls back to getRandomValues when randomUUID is unavailable", () => {
  vi.stubGlobal("crypto", {
    getRandomValues: (bytes: Uint8Array) => {
      bytes.fill(0);
      return bytes;
    },
  });
  expect(generateSessionId()).toMatch(
    /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-8[0-9a-f]{3}-[0-9a-f]{12}$/,
  );
});

it("has a last-resort fallback when Web Crypto is unavailable", () => {
  vi.stubGlobal("crypto", undefined);
  expect(generateSessionId()).toMatch(/^[a-z0-9]+-[a-z0-9]+-[a-z0-9]+$/);
});
