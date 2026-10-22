import { afterEach, expect, it, vi } from "vitest";
import { createReaderColumnId } from "@/lib/reader-column-id";

afterEach(() => vi.unstubAllGlobals());

it("uses the native UUID generator when it is available", () => {
  const uuid = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
  const randomUUID = vi.fn(() => uuid);
  const getRandomValues = vi.fn();
  vi.stubGlobal("crypto", { randomUUID, getRandomValues });
  expect(createReaderColumnId()).toBe(uuid);
  expect(randomUUID).toHaveBeenCalledOnce();
  expect(getRandomValues).not.toHaveBeenCalled();
});

it("uses secure random bytes with RFC 4122 version and variant bits when randomUUID is absent", () => {
  const getRandomValues = vi.fn((bytes: Uint8Array) => bytes.fill(255));
  vi.stubGlobal("crypto", { randomUUID: undefined, getRandomValues });
  expect(createReaderColumnId()).toBe("ffffffff-ffff-4fff-bfff-ffffffffffff");
  expect(getRandomValues).toHaveBeenCalledOnce();
  expect(getRandomValues.mock.calls[0][0]).toHaveLength(16);
});

it("uses fresh secure random bytes for each fallback column identity", () => {
  let sequence = 0;
  const getRandomValues = vi.fn((bytes: Uint8Array) => {
    bytes[15] = ++sequence;
    return bytes;
  });
  vi.stubGlobal("crypto", { randomUUID: undefined, getRandomValues });
  const identifiers = Array.from({ length: 3 }, createReaderColumnId);
  expect(new Set(identifiers).size).toBe(3);
  for (const id of identifiers)
    expect(id).toMatch(/^[\da-f]{8}-[\da-f]{4}-4[\da-f]{3}-[89ab][\da-f]{3}-[\da-f]{12}$/);
  expect(getRandomValues).toHaveBeenCalledTimes(3);
});

it("reports when secure randomness is unavailable rather than generating weak identifiers", () => {
  vi.stubGlobal("crypto", undefined);
  expect(createReaderColumnId).toThrow("Secure randomness is unavailable.");
});
