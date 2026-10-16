import { afterEach, expect, it, vi } from "vitest";
import { generateSessionId } from "@devfeed/telemetry/session";

afterEach(() => vi.unstubAllGlobals());

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

it("tries secure random bytes if UUID generation throws", () => {
  const getRandomValues = vi.fn((bytes: Uint8Array) => bytes.fill(1));
  vi.stubGlobal("crypto", {
    randomUUID: () => {
      throw new Error("restricted UUID");
    },
    getRandomValues,
  });
  expect(generateSessionId()).toBe("01010101-0101-4101-8101-010101010101");
  expect(getRandomValues).toHaveBeenCalledOnce();
});

it.each([
  undefined,
  {},
  {
    getRandomValues: () => {
      throw new Error("restricted crypto");
    },
  },
])("returns no identifier when cryptographic randomness is unavailable: %s", (cryptoApi) => {
  const random = vi.spyOn(Math, "random");
  vi.stubGlobal("crypto", cryptoApi);
  expect(generateSessionId()).toBe("");
  expect(random).not.toHaveBeenCalled();
});

it("returns no identifier when access to the crypto API throws", () => {
  vi.stubGlobal(
    "crypto",
    new Proxy(
      {},
      {
        get: () => {
          throw new Error("restricted crypto access");
        },
      },
    ),
  );
  expect(generateSessionId()).toBe("");
});
