import { afterEach, expect, it, vi } from "vitest";
vi.mock("server-only", () => ({}));
vi.mock("@/lib/server/config", () => ({ userApiOrigin: () => "http://user-api.test" }));
import { GET } from "@/app/api/v1/leaderboard/route";
afterEach(() => vi.unstubAllGlobals());

it("reads public standings without forwarding cookies, headers or query parameters", async () => {
  const value = { longest_streak: [], reading_days: [] };
  const fetcher = vi.fn().mockResolvedValue(Response.json(value));
  vi.stubGlobal("fetch", fetcher);
  const response = await GET(
    new Request("https://devfeed.tech/api/v1/leaderboard?user_id=private", {
      headers: { Cookie: "private-session", Authorization: "private" },
    }),
  );
  expect(await response.json()).toEqual(value);
  expect(response.headers.get("cache-control")).toBe("no-store");
  expect(fetcher).toHaveBeenCalledWith(
    "http://user-api.test/v1/user/leaderboard",
    expect.objectContaining({ credentials: "omit", cache: "no-store" }),
  );
  expect(fetcher.mock.calls[0][1].headers).toBeUndefined();
});

it("reports upstream failures rather than showing empty standings", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 503 })));
  expect((await GET(new Request("https://devfeed.tech/api/v1/leaderboard"))).status).toBe(503);
});
