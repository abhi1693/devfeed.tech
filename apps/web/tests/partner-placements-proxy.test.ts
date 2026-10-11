import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { POST } from "@/app/api/v1/partner-placements/query/route";
import { gateway } from "@/lib/server/gateway";
import { placement } from "./partner-placement-fixtures";

vi.mock("@devfeed/telemetry/propagation", () => ({ traceHeaders: () => ({}) }));
const query = { surface: "article", article_id: placement.product_id, exclude_product_ids: [] };
const request = (body: unknown = query) =>
  new Request("https://devfeed.tech/api/v1/partner-placements/query?url=evil", {
    method: "POST",
    body: typeof body === "string" ? body : JSON.stringify(body),
    headers: {
      Cookie: "private",
      Authorization: "private",
      "X-CSRF-Token": "private",
      Origin: "https://devfeed.tech",
    },
  });
beforeEach(() => {
  vi.stubEnv("DEVFEED_PUBLIC_API_URL", "http://public-api:8000");
  vi.stubEnv("DEVFEED_USER_API_URL", "http://user-api:8002");
  vi.stubEnv("DEVFEED_USER_BASE_URL", "https://devfeed.tech");
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation(async () => Response.json({ items: [placement] })),
  );
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

it("forwards validated context to its fixed upstream without identity or caching", async () => {
  vi.mocked(fetch).mockResolvedValue(
    Response.json({ items: [{ ...placement, private_partner_note: "private" }] }),
  );
  const response = await POST(request());
  expect(response.status).toBe(200);
  expect(await response.json()).toEqual({ items: [placement] });
  const [url, init] = vi.mocked(fetch).mock.calls[0];
  expect(String(url)).toBe("http://public-api:8000/v1/partner-placements/query");
  expect(init).toMatchObject({
    credentials: "omit",
    redirect: "manual",
    cache: "no-store",
    body: JSON.stringify(query),
    headers: { "Content-Type": "application/json" },
  });
  expect(response.headers.get("cache-control")).toBe("no-store");
});

it.each([
  "{",
  { surface: "article" },
  { surface: "feed", user_id: placement.product_id },
  { surface: "topic", topic_id: placement.product_id, article_id: placement.product_id },
  { surface: "feed", exclude_product_ids: Array(101).fill(placement.product_id) },
])("rejects invalid contexts before reaching the upstream (%j)", async (body) => {
  expect((await POST(request(body))).status).toBe(422);
  expect(fetch).not.toHaveBeenCalled();
});

it("bounds both public and authenticated query bodies before forwarding", async () => {
  expect((await POST(request(" ".repeat(8193)))).status).toBe(413);
  expect(
    (await gateway(request(" ".repeat(8193)), ["v1", "user", "partner-placements", "query"]))
      .status,
  ).toBe(413);
  expect(fetch).not.toHaveBeenCalled();
});

it.each([
  "http://partner.example/",
  "javascript:alert(1)",
  "https://user:password@partner.example/",
])("suppresses unsafe upstream links (%s)", async (url) => {
  vi.mocked(fetch).mockResolvedValue(Response.json({ items: [{ ...placement, url }] }));
  expect((await POST(request())).status).toBe(503);
});

it.each([302, 500])(
  "keeps failed or redirecting upstream responses private (%s)",
  async (status) => {
    vi.mocked(fetch).mockResolvedValue(new Response("private details", { status }));
    const response = await POST(request());
    expect(response.status).toBe(503);
    expect(await response.text()).not.toContain("private details");
  },
);

it("validates personalized contexts while retaining the session and CSRF boundary", async () => {
  expect(
    (
      await gateway(request({ surface: "feed", anything: "invalid" }), [
        "v1",
        "user",
        "partner-placements",
        "query",
      ])
    ).status,
  ).toBe(422);
  expect(fetch).not.toHaveBeenCalled();
  const personalized = new Request("https://devfeed.tech/api/v1/user/partner-placements/query", {
    method: "POST",
    body: JSON.stringify({ surface: "feed" }),
    headers: {
      Origin: "https://devfeed.tech",
      Cookie: "devfeed_user_session=session; admin_session=secret",
      "X-CSRF-Token": "csrf",
      "Content-Type": "application/json",
    },
  });
  expect((await gateway(personalized, ["v1", "user", "partner-placements", "query"])).status).toBe(
    200,
  );
  const [url, init] = vi.mocked(fetch).mock.calls[0];
  expect(url).toBe("http://user-api:8002/v1/user/partner-placements/query");
  expect(new Headers(init?.headers).get("cookie")).toBe("devfeed_user_session=session");
  expect(new Headers(init?.headers).get("x-csrf-token")).toBe("csrf");
});
