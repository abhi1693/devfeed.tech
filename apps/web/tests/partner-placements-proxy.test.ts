import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { POST } from "@/app/api/v1/partner-placements/query/route";
import * as personalized from "@/app/api/v1/user/partner-placements/query/route";
import { placement } from "./partner-placement-fixtures";
import { article } from "./fixtures";

// Unit requests have no Next response lifecycle; reader-gateway.mjs covers HTTP completion.
vi.mock("next/server", () => ({ after: (callback: () => void) => callback() }));

vi.mock("@devfeed/telemetry/propagation", () => ({ traceHeaders: () => ({}) }));
const query = { surface: "article", article_id: placement.product_id, exclude_product_ids: [] };
const sponsoredArticle = { ...article, topics: [], sponsor_names: ["Example partner"] };

it("projects only public platform identity and safe responsive logo variants", async () => {
  const platform = {
    name: "Launch platform",
    logo_url: "https://images.example/platform/32.webp?v=one",
    logo_variants: [
      { url: "https://images.example/platform/16.webp?v=one", width: 16 },
      { url: "https://images.example/platform/32.webp?v=one", width: 32 },
      { url: "javascript:alert(1)", width: 64 },
      { url: "https://secret@images.example/private.webp", width: 96 },
    ],
    provider: "private-provider-id",
    source_url: "https://remote.example/private-logo.png",
    encrypted_credential: "private",
  };
  vi.mocked(fetch).mockResolvedValue(Response.json({ items: [{ ...placement, platform }] }));
  const response = await POST(request(query));
  expect(response.status).toBe(200);
  expect((await response.json()).items[0].platform).toEqual({
    name: platform.name,
    logo_url: platform.logo_url,
    logo_variants: platform.logo_variants.slice(0, 2),
  });
});

it.each([undefined, {}, { name: "" }, { name: "x".repeat(201) }])(
  "rejects malformed platform metadata (%j)",
  async (platform) => {
    vi.mocked(fetch).mockResolvedValue(Response.json({ items: [{ ...placement, platform }] }));
    expect((await POST(request(query))).status).toBe(503);
  },
);

it.each([undefined, null, "", "   ", "  Via Nick Launches  ", "x".repeat(300)])(
  "preserves optional listing attribution (%j)",
  async (attribution) => {
    vi.mocked(fetch).mockResolvedValue(Response.json({ items: [{ ...placement, attribution }] }));
    const response = await POST(request(query));
    expect(response.status).toBe(200);
    expect((await response.json()).items[0].attribution).toBe(attribution?.trim() || null);
  },
);

it.each([123, {}, "x".repeat(301)])("rejects malformed attribution (%j)", async (attribution) => {
  vi.mocked(fetch).mockResolvedValue(Response.json({ items: [{ ...placement, attribution }] }));
  expect((await POST(request(query))).status).toBe(503);
});

it.each(["AI", "x".repeat(161)])("rejects an invalid product tagline (%s)", async (tagline) => {
  vi.mocked(fetch).mockResolvedValue(Response.json({ items: [{ ...placement, tagline }] }));
  expect((await POST(request(query))).status).toBe(503);
});

it("preserves a 160-character tagline and rejects legacy description-only deliveries", async () => {
  const tagline = "x".repeat(160);
  vi.mocked(fetch).mockResolvedValue(Response.json({ items: [{ ...placement, tagline }] }));
  const response = await POST(request(query));
  expect(response.status).toBe(200);
  expect((await response.json()).items[0].tagline).toBe(tagline);

  const { tagline: previous, ...legacy } = placement;
  vi.mocked(fetch).mockResolvedValue(
    Response.json({ items: [{ ...legacy, description: previous }] }),
  );
  expect((await POST(request(query))).status).toBe(503);
});

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
    ad_formats: ["feed", "sponsored_content", "engagement"],
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
  { surface: "feed", ad_formats: ["header_sponsorship"] },
  { surface: "article", article_id: placement.product_id, ad_formats: ["sponsored_content"] },
  { surface: "topic", topic_id: placement.product_id, ad_formats: ["header_sponsorship"] },
])("rejects unsupported or misplaced native format queries (%j)", async (body) => {
  expect((await POST(request(body))).status).toBe(422);
  expect(fetch).not.toHaveBeenCalled();
});

it("forwards an Engagement-only Search context with up to 24 visible article IDs", async () => {
  const body = {
    surface: "search",
    ad_formats: ["engagement"],
    allow_sponsored_fallback: false,
    article_ids: Array.from(
      { length: 24 },
      (_, index) => `aaaaaaaa-aaaa-4aaa-8aaa-${String(index).padStart(12, "0")}`,
    ),
  };
  vi.mocked(fetch).mockResolvedValue(
    Response.json({ items: [{ ...placement, ad_format: "engagement" }] }),
  );
  const response = await POST(request(body));
  expect(response.status).toBe(200);
  expect((await response.json()).items[0].ad_format).toBe("engagement");
  expect(JSON.parse(String(vi.mocked(fetch).mock.calls[0][1]?.body))).toEqual(body);
});

it("allows an empty Search context to return no products", async () => {
  vi.mocked(fetch).mockResolvedValue(Response.json({ items: [] }));
  const response = await POST(request({ surface: "search", ad_formats: ["engagement"] }));
  expect(response.status).toBe(200);
  expect(await response.json()).toEqual({ items: [] });
});

it("forwards search intent with up to 120 result identities across all visible kinds", async () => {
  const ids = Array.from(
    { length: 120 },
    (_, index) => `aaaaaaaa-aaaa-4aaa-8aaa-${String(index).padStart(12, "0")}`,
  );
  const body = {
    surface: "search",
    ad_formats: ["engagement"],
    allow_sponsored_fallback: false,
    search: {
      query: "TypeScript validation",
      results: {
        articles: ids.slice(0, 90),
        topics: ids.slice(90, 100),
        sources: ids.slice(100, 110),
        tags: ids.slice(110),
      },
    },
  };
  vi.mocked(fetch).mockResolvedValue(
    Response.json({ items: [{ ...placement, ad_format: "engagement" }] }),
  );
  const response = await POST(request(body));
  expect(response.status).toBe(200);
  expect((await response.json()).items[0].ad_format).toBe("engagement");
  expect(JSON.parse(String(vi.mocked(fetch).mock.calls[0][1]?.body))).toEqual(body);
});

it.each(["articles", "topics", "sources", "tags"])(
  "accepts a single %s result as a relevance opportunity",
  async (kind) => {
    const body = {
      surface: "search",
      ad_formats: ["engagement"],
      search: { query: "TypeScript", results: { [kind]: [placement.product_id] } },
    };
    vi.mocked(fetch).mockResolvedValue(Response.json({ items: [] }));
    expect((await POST(request(body))).status).toBe(200);
    expect(JSON.parse(String(vi.mocked(fetch).mock.calls[0][1]?.body))).toEqual(body);
  },
);

it("accepts the full normalized 200-character search query", async () => {
  const body = {
    surface: "search",
    ad_formats: ["engagement"],
    search: { query: "x".repeat(200), results: { articles: [placement.product_id] } },
  };
  vi.mocked(fetch).mockResolvedValue(Response.json({ items: [] }));
  expect((await POST(request(body))).status).toBe(200);
  expect(JSON.parse(String(vi.mocked(fetch).mock.calls[0][1]?.body))).toEqual(body);
});

it.each([
  null,
  [],
  { query: "TypeScript", results: {} },
  { query: "TypeScript", results: { articles: [] } },
  { query: "TypeScript", results: { users: [placement.product_id] } },
  { query: "TypeScript", results: { articles: "not-an-array" } },
  { query: "TypeScript", results: { articles: ["not-an-id"] } },
  { query: "TypeScript", results: { articles: [null] } },
  { query: "TypeScript", results: { articles: [placement.product_id, placement.product_id] } },
  { query: "TypeScript", results: { articles: [placement.product_id] }, approved: true },
  { query: 1, results: { articles: [placement.product_id] } },
  { query: "", results: { articles: [placement.product_id] } },
  { query: " ", results: { articles: [placement.product_id] } },
  { query: "---", results: { articles: [placement.product_id] } },
  { query: "Type\0Script", results: { articles: [placement.product_id] } },
  { query: "\ud800", results: { articles: [placement.product_id] } },
  { query: Array(21).fill("word").join(" "), results: { articles: [placement.product_id] } },
  { query: "x".repeat(201), results: { articles: [placement.product_id] } },
  {
    query: "TypeScript",
    results: {
      articles: Array.from(
        { length: 120 },
        (_, index) => `aaaaaaaa-aaaa-4aaa-8aaa-${String(index).padStart(12, "0")}`,
      ),
      topics: [placement.product_id],
    },
  },
])("rejects invalid or unbounded search intent before forwarding (%j)", async (search) => {
  expect(
    (await POST(request({ surface: "search", ad_formats: ["engagement"], search }))).status,
  ).toBe(422);
  expect(fetch).not.toHaveBeenCalled();
});

it.each([
  { surface: "feed" },
  { surface: "header", ad_formats: ["header_sponsorship"] },
  { surface: "article", article_id: placement.product_id },
  { surface: "topic", topic_id: placement.product_id },
  { surface: "search", article_id: placement.product_id },
  { surface: "search", topic_id: placement.product_id },
  { surface: "search", article_ids: [placement.product_id] },
])("does not combine structured search intent with another context (%j)", async (overrides) => {
  expect(
    (
      await POST(
        request({
          ad_formats: ["engagement"],
          search: { query: "TypeScript", results: { articles: [placement.product_id] } },
          ...overrides,
        }),
      )
    ).status,
  ).toBe(422);
  expect(fetch).not.toHaveBeenCalled();
});

it.each([
  {},
  { article_id: placement.product_id },
  { topic_id: placement.product_id },
  { article_ids: [placement.product_id] },
])("forwards Header sponsorship independently of feed fallback (%j)", async (context) => {
  const body = { surface: "header", ad_formats: ["header_sponsorship"], ...context };
  vi.mocked(fetch).mockResolvedValue(
    Response.json({ items: [{ ...placement, ad_format: "header_sponsorship" }] }),
  );
  const response = await POST(request(body));
  expect(response.status).toBe(200);
  expect((await response.json()).items[0].ad_format).toBe("header_sponsorship");
  expect(JSON.parse(String(vi.mocked(fetch).mock.calls[0][1]?.body))).toEqual(body);
});

it.each([
  { ad_formats: undefined },
  { ad_formats: ["feed"] },
  { ad_formats: ["header_sponsorship", "engagement"] },
  { ad_formats: ["homepage_sponsorship"] },
  { allow_sponsored_fallback: true },
  { article_id: "invalid" },
  { topic_id: "invalid" },
  { article_ids: Array(25).fill(placement.product_id) },
  { article_id: placement.product_id, topic_id: placement.product_id },
  { article_id: placement.product_id, article_ids: [placement.product_id] },
])("rejects invalid Header contexts before forwarding (%j)", async (overrides) => {
  expect(
    (
      await POST(
        request({
          surface: "header",
          ad_formats: ["header_sponsorship"],
          ...overrides,
        }),
      )
    ).status,
  ).toBe(422);
  expect(fetch).not.toHaveBeenCalled();
});

it.each([
  { ad_formats: undefined },
  { ad_formats: ["feed"] },
  { ad_formats: ["header_sponsorship"] },
  { ad_formats: ["sponsored_content"] },
  { ad_formats: ["engagement", "feed"] },
  { allow_sponsored_fallback: true },
  { article_id: placement.product_id },
  { topic_id: placement.product_id },
  { article_ids: Array(25).fill(placement.product_id) },
  { article_ids: ["not-an-article-id"] },
])("rejects invalid Search contexts before forwarding (%j)", async (overrides) => {
  const body = {
    surface: "search",
    ad_formats: ["engagement"],
    article_ids: [placement.product_id],
    ...overrides,
  };
  expect((await POST(request(body))).status).toBe(422);
  expect(fetch).not.toHaveBeenCalled();
});

it.each([
  { surface: "article", article_id: placement.product_id },
  { surface: "topic", topic_id: placement.product_id },
  { surface: "search", ad_formats: ["engagement"] },
])("preserves explicit false fallback on non-feed contexts (%j)", async (context) => {
  vi.mocked(fetch).mockResolvedValue(Response.json({ items: [] }));
  const body = { ...context, allow_sponsored_fallback: false };
  expect((await POST(request(body))).status).toBe(200);
  expect(JSON.parse(String(vi.mocked(fetch).mock.calls[0][1]?.body))).toEqual(body);
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
  vi.stubEnv("DEVFEED_PARTNER_CALLER_IP_HEADER", undefined);
  vi.stubEnv("DEVFEED_PARTNER_CALLER_KEY", undefined);
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

it("does not forward browser caller claims when trusted edge forwarding is disabled", async () => {
  const incoming = request();
  incoming.headers.set("X-DevFeed-Partner-Caller", "forged-proof");
  incoming.headers.set("X-Forwarded-For", "203.0.113.7");
  expect((await POST(incoming)).status).toBe(200);
  expect(vi.mocked(fetch).mock.calls[0][1]?.headers).toEqual({
    "Content-Type": "application/json",
  });
});

it("replaces browser caller claims with a proof bound to the placement destination", async () => {
  vi.stubEnv("DEVFEED_PARTNER_CALLER_IP_HEADER", "cf-connecting-ip");
  vi.stubEnv("DEVFEED_PARTNER_CALLER_KEY", "disposable-partner-caller-key-32-bytes");
  vi.spyOn(Date, "now").mockReturnValue(1_800_000_000_999);
  const incoming = request();
  incoming.headers.set("cf-connecting-ip", "203.0.113.7");
  incoming.headers.set("X-DevFeed-Partner-Caller", "forged-proof");
  incoming.headers.set("X-Forwarded-For", "198.51.100.1");
  expect((await POST(incoming)).status).toBe(200);
  expect(vi.mocked(fetch).mock.calls[0][1]?.headers).toEqual({
    "Content-Type": "application/json",
    "X-DevFeed-Partner-Caller":
      "v1.1800000000.203.0.113.7.cebe4cf3062024e2b0a188fb0dc46d40e61d42cbb52e21805fca33c2d8d79ada",
  });
});

it.each(["missing-edge-ip", "invalid-edge-ip", "missing-key"])(
  "fails closed before placement delivery when configured caller trust is invalid (%s)",
  async (mode) => {
    vi.stubEnv("DEVFEED_PARTNER_CALLER_IP_HEADER", "cf-connecting-ip");
    vi.stubEnv(
      "DEVFEED_PARTNER_CALLER_KEY",
      mode === "missing-key" ? undefined : "disposable-partner-caller-key-32-bytes",
    );
    const incoming = request();
    incoming.headers.set("X-DevFeed-Partner-Caller", "forged-proof");
    if (mode !== "missing-edge-ip")
      incoming.headers.set(
        "cf-connecting-ip",
        mode === "invalid-edge-ip" ? "203.0.113.7, 198.51.100.1" : "203.0.113.7",
      );
    const response = await POST(incoming);
    expect(response.status).toBe(503);
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(await response.json()).toEqual({ detail: "Partner recommendations unavailable" });
    expect(fetch).not.toHaveBeenCalled();
  },
);

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
  { surface: "topic", topic_id: placement.product_id, allow_sponsored_fallback: true },
])("rejects invalid contexts before reaching the upstream (%j)", async (body) => {
  expect((await POST(request(body))).status).toBe(422);
  expect(fetch).not.toHaveBeenCalled();
});

it("forwards the bounded visible article context for anonymous feed relevance", async () => {
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
  expect((await POST(request(" ".repeat(16_385)))).status).toBe(413);
  expect((await personalized.POST(request(" ".repeat(16_385)), {})).status).toBe(413);
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

it.each([null, "37"])(
  "preserves placement throttling and retry guidance without exposing upstream details (%s)",
  async (retryAfter) => {
    vi.mocked(fetch).mockResolvedValue(
      new Response("private infrastructure details", {
        status: 429,
        headers: retryAfter === null ? {} : { "Retry-After": retryAfter },
      }),
    );
    const response = await POST(request());
    expect(response.status).toBe(429);
    expect(response.headers.get("retry-after")).toBe(retryAfter);
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(await response.json()).toEqual({ detail: "Partner recommendations unavailable" });
  },
);

it("validates personalized contexts while retaining the session and CSRF boundary", async () => {
  expect(
    (await personalized.POST(request({ surface: "feed", anything: "invalid" }), {})).status,
  ).toBe(422);
  expect(fetch).not.toHaveBeenCalled();
  const incoming = new Request("https://devfeed.tech/api/v1/user/partner-placements/query", {
    method: "POST",
    body: JSON.stringify({ surface: "feed" }),
    headers: {
      Origin: "https://devfeed.tech",
      Cookie: "devfeed_user_session=session; admin_session=secret",
      "X-CSRF-Token": "csrf",
      "Content-Type": "application/json",
    },
  });
  expect((await personalized.POST(incoming, {})).status).toBe(200);
  const [url, init] = vi.mocked(fetch).mock.calls[0];
  expect(url).toBe("http://user-api:8002/v1/user/partner-placements/query");
  expect(new Headers(init?.headers).get("cookie")).toBe("devfeed_user_session=session");
  expect(new Headers(init?.headers).get("x-csrf-token")).toBe("csrf");
});
