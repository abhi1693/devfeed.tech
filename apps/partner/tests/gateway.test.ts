import { beforeEach, afterEach, expect, it, vi } from "vitest";
import { gateway } from "../src/lib/server/gateway";

vi.mock("@devfeed/telemetry/propagation", () => ({ traceHeaders: () => ({}) }));
beforeEach(() => vi.stubEnv("DEVFEED_PARTNER_API_URL", "http://partner-api:8004"));
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});
it("cannot proxy admin, user or traversal paths", async () => {
  const fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  for (const segments of [
    ["v1", "admin", "accounts"],
    ["v1", "user", "auth", "me"],
    ["v1", "partner", "..", "accounts"],
  ]) {
    expect((await gateway(new Request("https://partner.example/api"), segments)).status).toBe(404);
  }
  expect(fetchMock).not.toHaveBeenCalled();
});
it("rejects cross-origin mutations before calling the API", async () => {
  vi.stubEnv("DEVFEED_PARTNER_BASE_URL", "https://partner.example");
  const fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  expect(
    (
      await gateway(
        new Request("https://partner.example/api", {
          method: "POST",
          headers: { Origin: "https://evil.example" },
        }),
        ["v1", "partner", "auth", "logout"],
      )
    ).status,
  ).toBe(403);
  expect(fetchMock).not.toHaveBeenCalled();
});

it("rejects all partner reporting mutations even from its own origin", async () => {
  vi.stubEnv("DEVFEED_PARTNER_BASE_URL", "https://partner.example");
  const fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  const response = await gateway(
    new Request("https://partner.example/api", {
      method: "POST",
      headers: { Origin: "https://partner.example" },
    }),
    ["v1", "partner", "accounts"],
  );
  expect(response.status).toBe(405);
  expect(fetchMock).not.toHaveBeenCalled();
});

it("allows only the exact account preferences PUT and preserves origin and CSRF checks", async () => {
  vi.stubEnv("DEVFEED_PARTNER_BASE_URL", "https://partner.example");
  const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(Response.json({ ok: true })));
  vi.stubGlobal("fetch", fetchMock);
  const segments = [
    "v1",
    "partner",
    "accounts",
    "11111111-1111-4111-8111-111111111111",
    "preferences",
  ];
  const request = (method = "PUT", origin = "https://partner.example") =>
    new Request("https://partner.example/api", {
      method,
      headers: {
        origin,
        "x-csrf-token": "partner-csrf",
        "content-type": "application/json",
        cookie: "partner_session=private",
        authorization: "never-forward",
      },
      body: JSON.stringify({ outbound_destination: "listing_url" }),
    });
  expect((await gateway(request(), segments)).status).toBe(200);
  const [url, init] = fetchMock.mock.calls[0];
  expect(url).toBe(`http://partner-api:8004/${segments.join("/")}`);
  expect(init.method).toBe("PUT");
  expect(init.headers.get("x-csrf-token")).toBe("partner-csrf");
  expect(init.headers.get("cookie")).toBe("partner_session=private");
  expect(init.headers.has("authorization")).toBe(false);
  expect(JSON.parse(new TextDecoder().decode(init.body))).toEqual({
    outbound_destination: "listing_url",
  });
  fetchMock.mockClear();
  expect((await gateway(request("PUT", "https://evil.example"), segments)).status).toBe(403);
  for (const path of [
    segments.slice(0, -1),
    [...segments, "extra"],
    [...segments.slice(0, 3), "not-a-uuid", "preferences"],
  ]) {
    expect((await gateway(request(), path)).status).toBe(405);
  }
  for (const method of ["POST", "PATCH", "DELETE"])
    expect((await gateway(request(method), segments)).status).toBe(405);
  expect(fetchMock).not.toHaveBeenCalled();
});

it("forwards only allowed headers, preserves independent cookies and disables caching", async () => {
  vi.stubEnv("DEVFEED_PARTNER_BASE_URL", "https://partner.example");
  vi.stubEnv("DEVFEED_PARTNER_API_URL", "http://partner-api:8004");
  const upstream = new Response("ok", {
    headers: {
      "content-type": "text/plain",
      "x-request-id": "request-1",
      "x-private": "never-forward",
    },
  });
  upstream.headers.append("set-cookie", "session=new; HttpOnly");
  upstream.headers.append("set-cookie", "state=; Max-Age=0");
  const fetchMock = vi.fn().mockResolvedValue(upstream);
  vi.stubGlobal("fetch", fetchMock);
  const response = await gateway(
    new Request("https://partner.example/api?offset=25", {
      headers: { Cookie: "session=old", Authorization: "private", "if-none-match": "tag" },
    }),
    ["v1", "partner", "accounts"],
  );
  expect(await response.text()).toBe("ok");
  expect(response.headers.getSetCookie()).toHaveLength(2);
  expect(response.headers.get("cache-control")).toBe("no-store");
  expect(response.headers.get("x-request-id")).toBe("request-1");
  expect(response.headers.has("x-private")).toBe(false);
  const [url, init] = fetchMock.mock.calls[0];
  expect(url).toBe("http://partner-api:8004/v1/partner/accounts?offset=25");
  expect(init.headers.get("cookie")).toBe("session=old");
  expect(init.headers.get("if-none-match")).toBe("tag");
  expect(init.headers.has("authorization")).toBe(false);
  expect(init.redirect).toBe("manual");
  expect(init.cache).toBe("no-store");
});

it.each(["destination-preview", "creative-preview", "inventory"])(
  "allows only exact account %s GETs through the authenticated partner boundary",
  async (endpoint) => {
    vi.stubEnv("DEVFEED_PARTNER_BASE_URL", "https://partner.example");
    const fetchMock = vi.fn().mockImplementation(() => Response.json({ product: null }));
    vi.stubGlobal("fetch", fetchMock);
    const segments = [
      "v1",
      "partner",
      "accounts",
      "11111111-1111-4111-8111-111111111111",
      endpoint,
    ];
    const response = await gateway(
      new Request("https://partner.example/api", {
        headers: { cookie: "partner_session=private", authorization: "never-forward" },
      }),
      segments,
    );
    expect(response.status).toBe(200);
    expect(response.headers.get("cache-control")).toBe("no-store");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`http://partner-api:8004/${segments.join("/")}`);
    expect(init.method).toBe("GET");
    expect(init.cache).toBe("no-store");
    expect(init.headers.get("cookie")).toBe("partner_session=private");
    expect(init.headers.has("authorization")).toBe(false);
    expect(init.body).toBeUndefined();
    fetchMock.mockClear();
    for (const method of ["HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"])
      expect(
        (await gateway(new Request("https://partner.example/api", { method }), segments)).status,
      ).toBe(405);
    for (const path of [
      [...segments.slice(0, 3), "not-a-uuid", endpoint],
      [...segments, "extra"],
      ["v1", "partner", endpoint],
    ])
      expect((await gateway(new Request("https://partner.example/api"), path)).status).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();
  },
);

it("permits only exact owned-product policy reads and origin-checked partial PUTs", async () => {
  vi.stubEnv("DEVFEED_PARTNER_BASE_URL", "https://partner.example");
  const fetcher = vi.fn().mockImplementation(() => Response.json({ ok: true }));
  vi.stubGlobal("fetch", fetcher);
  const path = [
    "v1",
    "partner",
    "accounts",
    "11111111-1111-4111-8111-111111111111",
    "products",
    "33333333-3333-4333-8333-333333333333",
    "promotion",
  ];
  expect((await gateway(new Request("https://partner.example/api"), path)).status).toBe(200);
  const request = (origin: string) =>
    new Request("https://partner.example/api", {
      method: "PUT",
      headers: {
        origin,
        "content-type": "application/json",
        "x-csrf-token": "partner-csrf",
        cookie: "partner_session=private",
        authorization: "never-forward",
      },
      body: JSON.stringify({ ad_formats: [], promotion_surfaces: null }),
    });
  expect((await gateway(request("https://partner.example"), path)).status).toBe(200);
  const [url, init] = fetcher.mock.calls[1];
  expect(url).toBe(`http://partner-api:8004/${path.join("/")}`);
  expect(init.headers.get("x-csrf-token")).toBe("partner-csrf");
  expect(init.headers.get("cookie")).toBe("partner_session=private");
  expect(init.headers.has("authorization")).toBe(false);
  expect(JSON.parse(new TextDecoder().decode(init.body))).toEqual({
    ad_formats: [],
    promotion_surfaces: null,
  });
  fetcher.mockClear();
  expect((await gateway(request("https://evil.example"), path)).status).toBe(403);
  for (const invalid of [
    [...path, "extra"],
    [...path.slice(0, 5), "invalid-id", "promotion"],
    [...path.slice(0, 3), "invalid-id", ...path.slice(4)],
  ])
    expect((await gateway(request("https://partner.example"), invalid)).status).toBe(404);
  for (const method of ["POST", "PATCH", "DELETE", "HEAD"])
    expect(
      (await gateway(new Request("https://partner.example/api", { method }), path)).status,
    ).toBe(405);
  expect(fetcher).not.toHaveBeenCalled();
});

it("allows only exact audience-topic GETs and forwards server search parameters without credentials leaking", async () => {
  vi.stubEnv("DEVFEED_PARTNER_BASE_URL", "https://partner.example");
  const fetcher = vi.fn(async () => Response.json({ items: [], total: 0 }));
  vi.stubGlobal("fetch", fetcher);
  const segments = [
    "v1",
    "partner",
    "accounts",
    "11111111-1111-4111-8111-111111111111",
    "audience-topics",
  ];
  const response = await gateway(
    new Request("https://partner.example/api?dimension=tools&q=docker&offset=25&limit=25", {
      headers: { cookie: "partner_session=private", authorization: "never-forward" },
    }),
    segments,
  );
  expect(response.status).toBe(200);
  const [url, init] = fetcher.mock.calls[0] as unknown as [string, RequestInit];
  expect(url).toBe(
    `http://partner-api:8004/${segments.join("/")}?dimension=tools&q=docker&offset=25&limit=25`,
  );
  expect((init.headers as Headers).get("cookie")).toBe("partner_session=private");
  expect((init.headers as Headers).has("authorization")).toBe(false);
  expect(init.cache).toBe("no-store");
  fetcher.mockClear();
  for (const method of ["HEAD", "OPTIONS", "POST", "PUT", "DELETE"])
    expect(
      (await gateway(new Request("https://partner.example/api", { method }), segments)).status,
    ).toBe(405);
  for (const path of [
    [...segments, "extra"],
    [...segments.slice(0, 3), "not-a-uuid", "audience-topics"],
    ["v1", "partner", "audience-topics"],
  ])
    expect((await gateway(new Request("https://partner.example/api"), path)).status).toBe(404);
  expect(fetcher).not.toHaveBeenCalled();
});

it("forwards an audience-only preference without adding or clearing the destination", async () => {
  vi.stubEnv("DEVFEED_PARTNER_BASE_URL", "https://partner.example");
  const fetcher = vi.fn(async () => Response.json({ ok: true }));
  vi.stubGlobal("fetch", fetcher);
  const payload = {
    audience: { experience_levels: ["senior"], language_topic_ids: [], tool_topic_ids: [] },
  };
  const response = await gateway(
    new Request("https://partner.example/api", {
      method: "PUT",
      headers: {
        origin: "https://partner.example",
        "x-csrf-token": "csrf",
        "content-type": "application/json",
      },
      body: JSON.stringify(payload),
    }),
    ["v1", "partner", "accounts", "11111111-1111-4111-8111-111111111111", "preferences"],
  );
  expect(response.status).toBe(200);
  const init = (fetcher.mock.calls[0] as unknown as [string, RequestInit])[1];
  expect(JSON.parse(new TextDecoder().decode(init.body as ArrayBuffer))).toEqual(payload);
  expect((init.headers as Headers).get("x-csrf-token")).toBe("csrf");
});

it("forwards a bounded logout body and reports transport failures without credentials", async () => {
  vi.stubEnv("DEVFEED_PARTNER_BASE_URL", "https://partner.example");
  const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
  vi.stubGlobal("fetch", fetchMock);
  const request = () =>
    new Request("https://partner.example/api", {
      method: "POST",
      headers: {
        Origin: "https://partner.example",
        "x-csrf-token": "csrf",
        "content-type": "application/json",
      },
      body: "{}",
    });
  expect((await gateway(request(), ["v1", "partner", "auth", "logout"])).status).toBe(204);
  expect(new TextDecoder().decode(fetchMock.mock.calls[0][1].body)).toBe("{}");
  expect(fetchMock.mock.calls[0][1].headers.get("x-csrf-token")).toBe("csrf");
  fetchMock.mockRejectedValue(new Error("secret upstream URL"));
  const failed = await gateway(request(), ["v1", "partner", "auth", "logout"]);
  expect(failed.status).toBe(503);
  expect(await failed.json()).toEqual({ detail: "Partner service unavailable" });
});

it.each([true, false])(
  "rejects oversized declared or streamed logout bodies (%s)",
  async (declared) => {
    vi.stubEnv("DEVFEED_PARTNER_BASE_URL", "https://partner.example");
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const headers = new Headers({ Origin: "https://partner.example" });
    if (declared) headers.set("content-length", "1000001");
    const response = await gateway(
      new Request("https://partner.example/api", {
        method: "POST",
        headers,
        body: "x".repeat(1000001),
      }),
      ["v1", "partner", "auth", "logout"],
    );
    expect(response.status).toBe(413);
    expect(fetchMock).not.toHaveBeenCalled();
  },
);
