import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { POST } from "@/app/api/v1/partner-placements/query/route";
import { gateway } from "@/lib/server/gateway";
import { placement } from "./partner-placement-fixtures";
import { article } from "./fixtures";

vi.mock("@devfeed/telemetry/propagation", () => ({ traceHeaders: () => ({}) }));
const query = { surface: "article", article_id: placement.product_id, exclude_product_ids: [] };
const sponsoredArticle = { ...article, topics: [], sponsor_names: ["Example partner"] };

it("forwards only the requested native formats and projects a real sponsored article", async () => {
  const item = {
    ...placement,
    ad_format: "sponsored_content",
    article: { ...sponsoredArticle, moderation_notes: "private" },
    url: `/articles/${article.slug}`,
  };
  vi.mocked(fetch).mockResolvedValue(Response.json({ items: [item] }));
  const body = {
    surface: "feed",
    ad_formats: ["feed", "homepage_sponsorship", "sponsored_content", "engagement"],
    allow_sponsored_fallback: true,
  };
  const response = await POST(request(body));
  expect(response.status).toBe(200);
  const output = await response.json();
  expect(output.items[0].article.id).toBe(article.id);
  expect(output.items[0].article.sponsor_names).toEqual(["Example partner"]);
  expect(output.items[0].article.moderation_notes).toBeUndefined();
  expect(output.items[0].url).toBe(`/articles/${article.slug}`);
  expect(JSON.parse(String(vi.mocked(fetch).mock.calls[0][1]?.body))).toEqual(body);
});

it("preserves valid long-form editorial text from the full ArticleOut contract", async () => {
  const longArticle = {
    ...sponsoredArticle,
    summary: "Tutorial details. ".repeat(5000),
    ai_summary: "Editorial overview. ".repeat(3000),
    ai_description: "Editorial description. ".repeat(3000),
    sources: sponsoredArticle.sources.map((source) => ({
      ...source,
      description: "Publisher overview. ".repeat(3000),
    })),
  };
  vi.mocked(fetch).mockResolvedValue(
    Response.json({
      items: [
        {
          ...placement,
          ad_format: "sponsored_content",
          article: longArticle,
          url: `/articles/${article.slug}`,
        },
      ],
    }),
  );
  const response = await POST(request({ surface: "feed", ad_formats: ["sponsored_content"] }));
  expect(response.status).toBe(200);
  const delivered = (await response.json()).items[0].article;
  expect(delivered.summary).toBe(longArticle.summary);
  expect(delivered.ai_summary).toBe(longArticle.ai_summary);
  expect(delivered.ai_description).toBe(longArticle.ai_description);
  expect(delivered.sources[0].description).toBe(longArticle.sources[0].description);
});

it.each([
  { surface: "feed", ad_formats: [] },
  { surface: "feed", ad_formats: ["newsletter"] },
  { surface: "feed", ad_formats: ["feed", "feed"] },
  { surface: "feed", ad_formats: ["homepage_sponsorship"] },
  { surface: "article", article_id: placement.product_id, ad_formats: ["sponsored_content"] },
  { surface: "topic", topic_id: placement.product_id, ad_formats: ["homepage_sponsorship"] },
])("rejects unsupported or misplaced native format queries (%j)", async (body) => {
  expect((await POST(request(body))).status).toBe(422);
  expect(fetch).not.toHaveBeenCalled();
});

it.each([
  { ad_format: "agent", article: null },
  { ad_format: "sponsored_content", article: null },
  {
    ad_format: "sponsored_content",
    article: sponsoredArticle,
    url: "https://partner.example/external",
  },
  {
    ad_format: "sponsored_content",
    article: { ...sponsoredArticle, slug: "../private" },
    url: "/articles/../private",
  },
  { ad_format: "feed", article: null, url: `/articles/${article.slug}` },
])("rejects invalid creative destinations or editorial payloads (%j)", async (overrides) => {
  vi.mocked(fetch).mockResolvedValue(Response.json({ items: [{ ...placement, ...overrides }] }));
  expect((await POST(request())).status).toBe(503);
});
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

it("accepts deliveries without exposure limits and discards legacy cap metadata", async () => {
  vi.mocked(fetch).mockResolvedValue(
    Response.json({ items: [{ ...placement, frequency_cap: 1 }] }),
  );
  const response = await POST(request());
  expect(response.status).toBe(200);
  expect(await response.json()).toEqual({ items: [placement] });
});

it.each([
  "{",
  { surface: "article" },
  { surface: "feed", user_id: placement.product_id },
  { surface: "topic", topic_id: placement.product_id, article_id: placement.product_id },
  { surface: "feed", exclude_product_ids: Array(101).fill(placement.product_id) },
  { surface: "feed", article_ids: Array(25).fill(placement.product_id) },
  { surface: "feed", article_ids: ["not-an-article-id"] },
  { surface: "article", article_id: placement.product_id, article_ids: [placement.product_id] },
  { surface: "topic", topic_id: placement.product_id, article_ids: [placement.product_id] },
  { surface: "feed", allow_sponsored_fallback: "true" },
  { surface: "feed", allow_sponsored_fallback: 1 },
  { surface: "article", article_id: placement.product_id, allow_sponsored_fallback: false },
  { surface: "topic", topic_id: placement.product_id, allow_sponsored_fallback: true },
])("rejects invalid contexts before reaching the upstream (%j)", async (body) => {
  expect((await POST(request(body))).status).toBe(422);
  expect(fetch).not.toHaveBeenCalled();
});

it("forwards the bounded visible article context for anonymous feed and search relevance", async () => {
  const context = {
    surface: "feed",
    article_ids: [placement.product_id, sponsoredId],
    exclude_product_ids: [],
  };
  expect((await POST(request(context))).status).toBe(200);
  expect(JSON.parse(String(vi.mocked(fetch).mock.calls[0][1]?.body))).toEqual(context);
});

const sponsoredId = "33333333-3333-4333-8333-333333333333";

it.each([true, false])(
  "preserves the explicit main-feed fallback opt-in (%s)",
  async (allow_sponsored_fallback) => {
    const body = { surface: "feed", article_ids: [placement.product_id], allow_sponsored_fallback };
    expect((await POST(request(body))).status).toBe(200);
    expect(JSON.parse(String(vi.mocked(fetch).mock.calls[0][1]?.body))).toEqual(body);
  },
);

it("keeps a product when its optional logo is absent", async () => {
  vi.mocked(fetch).mockResolvedValue(Response.json({ items: [{ ...placement, logo_url: null }] }));
  const response = await POST(request());
  expect(response.status).toBe(200);
  expect(await response.json()).toEqual({ items: [{ ...placement, logo_url: null }] });
});

it("preserves managed OG images and responsive variants without exposing upstream metadata", async () => {
  const image_url = "https://assets.partner.example/images/original.png";
  const image_variants = [{ url: "https://assets.partner.example/images/640.webp", width: 640 }];
  vi.mocked(fetch).mockResolvedValue(
    Response.json({
      items: [
        {
          ...placement,
          image_url,
          image_variants,
          private_source_image_url: "https://private.example/source.png",
        },
      ],
    }),
  );
  const response = await POST(request());
  expect(response.status).toBe(200);
  expect(await response.json()).toEqual({ items: [{ ...placement, image_url, image_variants }] });
});

it("accepts older deliveries without image fields and uses the logo fallback", async () => {
  const older: Partial<typeof placement> = { ...placement };
  delete older.image_url;
  delete older.image_variants;
  vi.mocked(fetch).mockResolvedValue(Response.json({ items: [older] }));
  const response = await POST(request());
  expect(response.status).toBe(200);
  expect(await response.json()).toEqual({
    items: [{ ...placement, image_url: null, image_variants: [] }],
  });
});

it.each([
  "javascript:alert(1)",
  "http://partner.example/hero.png",
  "https://user:password@partner.example/hero.png",
])("ignores unsafe OG hero URLs without dropping the product (%s)", async (image_url) => {
  vi.mocked(fetch).mockResolvedValue(Response.json({ items: [{ ...placement, image_url }] }));
  const response = await POST(request());
  expect(response.status).toBe(200);
  expect(await response.json()).toEqual({ items: [{ ...placement, image_url: null }] });
});

it("removes unsafe or malformed responsive image variants", async () => {
  const valid = { url: "https://assets.partner.example/images/640.webp", width: 640 };
  vi.mocked(fetch).mockResolvedValue(
    Response.json({
      items: [
        {
          ...placement,
          image_variants: [
            valid,
            { url: "javascript:alert(1)", width: 400 },
            { url: "https://user:password@assets.partner.example/800.webp", width: 800 },
            { url: valid.url, width: 0 },
            { url: valid.url, width: 400.5 },
            null,
          ],
        },
      ],
    }),
  );
  const response = await POST(request());
  expect(response.status).toBe(200);
  expect(await response.json()).toEqual({ items: [{ ...placement, image_variants: [valid] }] });
});

it.each([
  undefined,
  "javascript:alert(1)",
  "http://partner.example/logo.png",
  "https://user:password@partner.example/logo.png",
])("uses a logo fallback without dropping a relevant product (%s)", async (logo_url) => {
  vi.mocked(fetch).mockResolvedValue(Response.json({ items: [{ ...placement, logo_url }] }));
  const response = await POST(request());
  expect(response.status).toBe(200);
  expect(await response.json()).toEqual({ items: [{ ...placement, logo_url: null }] });
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
