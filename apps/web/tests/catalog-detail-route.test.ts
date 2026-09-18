import { afterEach, expect, it, vi } from "vitest";
import { GET as topics } from "@/app/api/v1/topics/[slug]/route";
import { GET as sources } from "@/app/api/v1/sources/[slug]/route";
afterEach(() => vi.unstubAllGlobals());
it.each([
  ["topics", topics],
  ["sources", sources],
] as const)("looks up one %s detail without scanning catalog pages", async (kind, get) => {
  const item = { id: "one", slug: "late-item" };
  const fetcher = vi.fn().mockResolvedValue(Response.json(item));
  vi.stubGlobal("fetch", fetcher);
  const response = await get(new Request(`https://test/api/v1/${kind}/late-item`), {
    params: Promise.resolve({ slug: "late-item" }),
  });
  expect(await response.json()).toEqual(item);
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(fetcher.mock.calls[0][0].pathname).toBe(`/v1/${kind}/late-item`);
  const invalid = await get(new Request("https://test"), {
    params: Promise.resolve({ slug: "../private" }),
  });
  expect(invalid.status).toBe(404);
  expect(fetcher).toHaveBeenCalledTimes(1);
});
