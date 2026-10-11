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

it("allows only exact account preview GETs through the authenticated partner boundary", async () => {
  vi.stubEnv("DEVFEED_PARTNER_BASE_URL", "https://partner.example");
  const fetchMock = vi.fn().mockImplementation(() => Response.json({ product: null }));
  vi.stubGlobal("fetch", fetchMock);
  const segments = [
    "v1",
    "partner",
    "accounts",
    "11111111-1111-4111-8111-111111111111",
    "destination-preview",
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
    [...segments.slice(0, 3), "not-a-uuid", "destination-preview"],
    [...segments, "extra"],
    ["v1", "partner", "destination-preview"],
  ])
    expect((await gateway(new Request("https://partner.example/api"), path)).status).toBe(404);
  expect(fetchMock).not.toHaveBeenCalled();
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
