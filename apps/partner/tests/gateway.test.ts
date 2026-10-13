import { beforeEach, afterEach, expect, it, vi } from "vitest";
import { gateway } from "../src/lib/server/gateway";

vi.mock("@devfeed/telemetry/propagation", () => ({ traceHeaders: () => ({}) }));
const productAdImage = [
  "v1",
  "partner",
  "accounts",
  "11111111-1111-4111-8111-111111111111",
  "products",
  "22222222-2222-4222-8222-222222222222",
  "ad-image",
];
const manualProducts = productAdImage.slice(0, 5);
const manualProduct = productAdImage.slice(0, 6);
beforeEach(() => vi.stubEnv("DEVFEED_PARTNER_API_URL", "http://partner-api:8004"));
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

it("forwards a product image multipart upload without changing its boundary or bytes", async () => {
  vi.stubEnv("DEVFEED_PARTNER_BASE_URL", "https://partner.example");
  const fetcher = vi.fn().mockResolvedValue(Response.json({ ad_image_status: "ready" }));
  vi.stubGlobal("fetch", fetcher);
  const body = new FormData();
  body.append("file", new Blob([new Uint8Array(1_100_000)], { type: "image/png" }), "ad.png");
  const incoming = new Request("https://partner.example/api", {
    method: "POST",
    headers: { origin: "https://partner.example", "x-csrf-token": "csrf" },
    body,
  });
  const expectedBytes = await incoming.clone().arrayBuffer();
  expect((await gateway(incoming, productAdImage)).status).toBe(200);
  const [url, init] = fetcher.mock.calls[0];
  expect(url).toBe(`http://partner-api:8004/${productAdImage.join("/")}`);
  expect(init.headers.get("content-type")).toBe(incoming.headers.get("content-type"));
  expect(init.headers.get("x-csrf-token")).toBe("csrf");
  expect(Buffer.from(init.body).equals(Buffer.from(expectedBytes))).toBe(true);
});

it("rejects foreign origins and malformed image upload scopes before reading the body", async () => {
  vi.stubEnv("DEVFEED_PARTNER_BASE_URL", "https://partner.example");
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  for (const [path, origin, status] of [
    [productAdImage, "https://evil.example", 403],
    [[...productAdImage, "extra"], "https://partner.example", 404],
    [
      [...productAdImage.slice(0, 3), "foreign", ...productAdImage.slice(4)],
      "https://partner.example",
      404,
    ],
  ] as const) {
    const incoming = new Request("https://partner.example/api", {
      method: "POST",
      headers: { origin },
      body: new ReadableStream<Uint8Array>(),
      duplex: "half",
    } as RequestInit);
    expect((await gateway(incoming, [...path])).status).toBe(status);
    expect(incoming.bodyUsed).toBe(false);
  }
  for (const method of ["GET", "PUT", "DELETE"]) {
    expect(
      (await gateway(new Request("https://partner.example/api", { method }), productAdImage))
        .status,
    ).toBe(405);
  }
  expect(fetcher).not.toHaveBeenCalled();
});

it.each([true, false])(
  "caps declared and chunked product image bodies (declared=%s)",
  async (declared) => {
    vi.stubEnv("DEVFEED_PARTNER_BASE_URL", "https://partner.example");
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    const bytes = 5 * 1024 * 1024 + 65537;
    const incoming = new Request("https://partner.example/api", {
      method: "POST",
      headers: {
        origin: "https://partner.example",
        ...(declared ? { "content-length": String(bytes) } : {}),
      },
      body: new Uint8Array(bytes),
    });
    expect((await gateway(incoming, productAdImage)).status).toBe(413);
    expect(fetcher).not.toHaveBeenCalled();
  },
);

it("times out an incomplete product image upload and cancels its body reader", async () => {
  vi.useFakeTimers();
  vi.stubEnv("DEVFEED_PARTNER_BASE_URL", "https://partner.example");
  const fetcher = vi.fn();
  const cancel = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  const incoming = new Request("https://partner.example/api", {
    method: "POST",
    headers: { origin: "https://partner.example" },
    body: new ReadableStream<Uint8Array>({ cancel }),
    duplex: "half",
  } as RequestInit);
  const pending = gateway(incoming, productAdImage);
  await vi.advanceTimersByTimeAsync(10_000);
  const response = await pending;
  expect(response.status).toBe(408);
  expect(response.headers.get("cache-control")).toBe("no-store");
  expect(cancel).toHaveBeenCalledOnce();
  expect(fetcher).not.toHaveBeenCalled();
  expect(incoming.body!.locked).toBe(false);
  expect(vi.getTimerCount()).toBe(0);
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

it.each([
  { path: manualProducts, methods: ["POST"] },
  { path: manualProduct, methods: ["GET", "PATCH", "DELETE"] },
])("restricts manual product methods at $path", async ({ path, methods }) => {
  vi.stubEnv("DEVFEED_PARTNER_BASE_URL", "https://partner.example");
  const fetcher = vi.fn().mockImplementation(() => Response.json({ id: manualProduct[5] }));
  vi.stubGlobal("fetch", fetcher);
  for (const method of ["GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"]) {
    fetcher.mockClear();
    const response = await gateway(
      new Request("https://partner.example/api", {
        method,
        headers: { origin: "https://partner.example", "x-csrf-token": "csrf" },
      }),
      path,
    );
    expect(response.status).toBe(methods.includes(method) ? 200 : 405);
    if (methods.includes(method)) {
      expect(fetcher).toHaveBeenCalledOnce();
      expect(fetcher.mock.calls[0][0]).toBe(`http://partner-api:8004/${path.join("/")}`);
      expect(fetcher.mock.calls[0][1].headers.get("x-csrf-token")).toBe("csrf");
    } else expect(fetcher).not.toHaveBeenCalled();
  }
});

it("rejects malformed product scopes and foreign origins before reading product facts", async () => {
  vi.stubEnv("DEVFEED_PARTNER_BASE_URL", "https://partner.example");
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  for (const [path, origin, status] of [
    [[...manualProduct, "members"], "https://partner.example", 404],
    [[...manualProducts.slice(0, 3), "foreign", "products"], "https://partner.example", 404],
    [manualProducts, "https://evil.example", 403],
  ] as const) {
    const incoming = new Request("https://partner.example/api", {
      method: "POST",
      headers: { origin },
      body: new ReadableStream<Uint8Array>(),
      duplex: "half",
    } as RequestInit);
    expect((await gateway(incoming, [...path])).status).toBe(status);
    expect(incoming.bodyUsed).toBe(false);
  }
  expect(fetcher).not.toHaveBeenCalled();
});

it.each([true, false])(
  "bounds manual product JSON before forwarding (declared=%s)",
  async (declared) => {
    vi.stubEnv("DEVFEED_PARTNER_BASE_URL", "https://partner.example");
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    expect(
      (
        await gateway(
          new Request("https://partner.example/api", {
            method: "POST",
            headers: {
              origin: "https://partner.example",
              ...(declared ? { "content-length": "65537" } : {}),
            },
            body: new Uint8Array(65537),
          }),
          manualProducts,
        )
      ).status,
    ).toBe(413);
    expect(fetcher).not.toHaveBeenCalled();
  },
);

it("preserves multilingual product facts within the bounded write body", async () => {
  vi.stubEnv("DEVFEED_PARTNER_BASE_URL", "https://partner.example");
  const fetcher = vi.fn().mockResolvedValue(Response.json({}));
  vi.stubGlobal("fetch", fetcher);
  const facts = { name: "Product", description: "界".repeat(5000), expected_revision: 2 };
  const response = await gateway(
    new Request("https://partner.example/api", {
      method: "PATCH",
      headers: { origin: "https://partner.example", "content-type": "application/json" },
      body: JSON.stringify(facts),
    }),
    manualProduct,
  );
  expect(response.status).toBe(200);
  expect(JSON.parse(new TextDecoder().decode(fetcher.mock.calls[0][1].body))).toEqual(facts);
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
      ...(endpoint === "inventory" ? [] : [["v1", "partner", endpoint]]),
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

it.each(["resolves", "stalls", "rejects"])(
  "returns 408 at the upload deadline even when stream cancellation %s",
  async (behavior) => {
    vi.useFakeTimers();
    vi.stubEnv("DEVFEED_PARTNER_BASE_URL", "https://partner.example");
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    const cancel = vi.fn(() => {
      if (behavior === "stalls") return new Promise<void>(() => {});
      if (behavior === "rejects") return Promise.reject(new Error("Private peer details"));
      return Promise.resolve();
    });
    const incoming = new Request("https://partner.example/api", {
      method: "POST",
      headers: { origin: "https://partner.example", cookie: "session=private" },
      body: new ReadableStream<Uint8Array>({ cancel }),
      duplex: "half",
    } as RequestInit);
    const completed = vi.fn();
    const pending = gateway(incoming, ["v1", "partner", "auth", "logout"]);
    void pending.then(completed);
    await vi.advanceTimersByTimeAsync(1999);
    expect(completed).not.toHaveBeenCalled();
    expect(cancel).not.toHaveBeenCalled();
    expect(fetcher).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(completed).toHaveBeenCalledOnce();
    const response = await pending;
    expect(response.status).toBe(408);
    expect(await response.json()).toEqual({ detail: "Incomplete request" });
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(cancel).toHaveBeenCalledOnce();
    expect(incoming.body!.locked).toBe(false);
    expect(fetcher).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  },
);

it("uses one upload deadline even when partial preference chunks keep arriving", async () => {
  vi.useFakeTimers();
  vi.stubEnv("DEVFEED_PARTNER_BASE_URL", "https://partner.example");
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const cancel = vi.fn();
  const incoming = new Request("https://partner.example/api", {
    method: "PUT",
    headers: { origin: "https://partner.example", "content-type": "application/json" },
    body: new ReadableStream<Uint8Array>({
      start(value) {
        controller = value;
      },
      cancel,
    }),
    duplex: "half",
  } as RequestInit);
  const pending = gateway(incoming, [
    "v1",
    "partner",
    "accounts",
    "11111111-1111-4111-8111-111111111111",
    "preferences",
  ]);
  const completed = vi.fn();
  void pending.then(completed);
  await vi.advanceTimersByTimeAsync(1000);
  controller.enqueue(new TextEncoder().encode('{"outbound_destination":'));
  await vi.advanceTimersByTimeAsync(999);
  controller.enqueue(new TextEncoder().encode('"listing_url"'));
  expect(completed).not.toHaveBeenCalled();
  expect(fetcher).not.toHaveBeenCalled();
  await vi.advanceTimersByTimeAsync(1);
  expect(completed).toHaveBeenCalledOnce();
  const response = await pending;
  expect(response.status).toBe(408);
  expect(response.headers.get("cache-control")).toBe("no-store");
  expect(cancel).toHaveBeenCalledOnce();
  expect(incoming.body!.locked).toBe(false);
  expect(fetcher).not.toHaveBeenCalled();
});

it.each([
  { declared: true, cancellation: "stalls" },
  { declared: true, cancellation: "rejects" },
  { declared: false, cancellation: "stalls" },
  { declared: false, cancellation: "rejects" },
])(
  "keeps oversized uploads at 413 when cancellation $cancellation (declared=$declared)",
  async ({ declared, cancellation }) => {
    vi.useFakeTimers();
    vi.stubEnv("DEVFEED_PARTNER_BASE_URL", "https://partner.example");
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    const cancel = vi.fn(() =>
      cancellation === "stalls"
        ? new Promise<void>(() => {})
        : Promise.reject(new Error("Private peer details")),
    );
    const incoming = new Request("https://partner.example/api", {
      method: "POST",
      headers: {
        origin: "https://partner.example",
        "content-length": declared ? "1000001" : "1",
      },
      body: new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(new TextEncoder().encode("é".repeat(250_000)));
          controller.enqueue(new TextEncoder().encode("é".repeat(250_001)));
        },
        cancel,
      }),
      duplex: "half",
    } as RequestInit);
    const completed = vi.fn();
    const pending = gateway(incoming, ["v1", "partner", "auth", "logout"]);
    void pending.then(completed);
    await vi.advanceTimersByTimeAsync(0);
    expect(completed).toHaveBeenCalledOnce();
    const response = await pending;
    expect(response.status).toBe(413);
    expect(await response.json()).toEqual({ detail: "Request too large" });
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(cancel).toHaveBeenCalledOnce();
    expect(incoming.body!.locked).toBe(false);
    expect(fetcher).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  },
);

it("forwards a completed chunked upload at the byte limit and clears its deadline", async () => {
  vi.useFakeTimers();
  vi.stubEnv("DEVFEED_PARTNER_BASE_URL", "https://partner.example");
  const fetcher = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
  vi.stubGlobal("fetch", fetcher);
  const value = "é".repeat(500_000);
  const bytes = new TextEncoder().encode(value);
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const cancel = vi.fn();
  const incoming = new Request("https://partner.example/api", {
    method: "POST",
    headers: { origin: "https://partner.example", "x-csrf-token": "csrf" },
    body: new ReadableStream<Uint8Array>({
      start(value) {
        controller = value;
      },
      cancel,
    }),
    duplex: "half",
  } as RequestInit);
  const pending = gateway(incoming, ["v1", "partner", "auth", "logout"]);
  await vi.advanceTimersByTimeAsync(400);
  controller.enqueue(bytes.slice(0, 400_001));
  await vi.advanceTimersByTimeAsync(400);
  expect(fetcher).not.toHaveBeenCalled();
  controller.enqueue(bytes.slice(400_001));
  controller.close();
  expect((await pending).status).toBe(204);
  expect(fetcher).toHaveBeenCalledOnce();
  const [url, init] = fetcher.mock.calls[0];
  expect(url).toBe("http://partner-api:8004/v1/partner/auth/logout");
  expect(init.body.byteLength).toBe(1_000_000);
  expect(new TextDecoder().decode(init.body)).toBe(value);
  expect(init.headers.get("x-csrf-token")).toBe("csrf");
  expect(init.cache).toBe("no-store");
  expect(init.redirect).toBe("manual");
  expect(incoming.body!.locked).toBe(false);
  expect(vi.getTimerCount()).toBe(0);
  await vi.advanceTimersByTimeAsync(2000);
  expect(cancel).not.toHaveBeenCalled();
  expect(fetcher).toHaveBeenCalledOnce();
});

const accountConnections = [
  "v1",
  "partner",
  "accounts",
  "11111111-1111-4111-8111-111111111111",
  "connections",
];

it.each(["inventory", "dashboard"])("allows only exact workspace %s GETs", async (endpoint) => {
  vi.stubEnv("DEVFEED_PARTNER_BASE_URL", "https://partner.example");
  const fetcher = vi.fn().mockImplementation(() => Response.json({ items: [] }));
  vi.stubGlobal("fetch", fetcher);
  const path = ["v1", "partner", endpoint];
  expect(
    (
      await gateway(
        new Request(
          "https://partner.example/api?account_id=11111111-1111-4111-8111-111111111111&q=catalog",
        ),
        path,
      )
    ).status,
  ).toBe(200);
  expect(fetcher.mock.calls[0][0]).toContain(`/v1/partner/${endpoint}?account_id=`);
  fetcher.mockClear();
  for (const method of ["HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"]) {
    expect(
      (await gateway(new Request("https://partner.example/api", { method }), path)).status,
    ).toBe(405);
  }
  expect(
    (await gateway(new Request("https://partner.example/api"), [...path, "extra"])).status,
  ).toBe(404);
  expect(fetcher).not.toHaveBeenCalled();
});

it("requires a trusted origin for explicit workspace connection creation", async () => {
  vi.stubEnv("DEVFEED_PARTNER_BASE_URL", "https://partner.example");
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  expect(
    (
      await gateway(
        new Request("https://partner.example/api", {
          method: "POST",
          headers: { origin: "https://evil.example", "x-csrf-token": "csrf" },
          body: "{}",
        }),
        ["v1", "partner", "connections"],
      )
    ).status,
  ).toBe(403);
  expect(fetcher).not.toHaveBeenCalled();
});

it.each([
  { suffix: [], allowed: ["GET", "POST"] },
  { suffix: ["source"], allowed: ["GET"] },
  { suffix: ["source", "jobs"], allowed: ["GET"] },
])(
  "permits only workspace connection reads and explicit creation at $suffix",
  async ({ suffix, allowed }) => {
    vi.stubEnv("DEVFEED_PARTNER_BASE_URL", "https://partner.example");
    const fetcher = vi.fn().mockImplementation(() => Response.json({ ok: true }));
    vi.stubGlobal("fetch", fetcher);
    const segments = ["v1", "partner", "connections", ...suffix];
    for (const method of ["GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"]) {
      fetcher.mockClear();
      expect(
        (
          await gateway(
            new Request("https://partner.example/api", {
              method,
              headers: {
                origin: "https://partner.example",
                "x-csrf-token": "csrf",
                authorization: "never-forward",
              },
            }),
            segments,
          )
        ).status,
      ).toBe(allowed.includes(method) ? 200 : 405);
      if (allowed.includes(method)) {
        expect(fetcher).toHaveBeenCalledOnce();
        expect(fetcher.mock.calls[0][1].headers.has("authorization")).toBe(false);
        expect(fetcher.mock.calls[0][1].headers.get("x-csrf-token")).toBe("csrf");
      } else expect(fetcher).not.toHaveBeenCalled();
    }
  },
);

it.each([
  { suffix: [], allowed: ["GET", "POST"] },
  { suffix: ["nick-launches"], allowed: ["GET", "PUT", "DELETE"] },
  { suffix: ["nick-launches", "actions"], allowed: ["POST"] },
  { suffix: ["nick-launches", "jobs"], allowed: ["GET"] },
  { suffix: ["inventory"], allowed: ["GET", "PUT", "DELETE"] },
  { suffix: ["a".repeat(200)], allowed: ["GET", "PUT", "DELETE"] },
])("allows only declared connection methods at $suffix", async ({ suffix, allowed }) => {
  vi.stubEnv("DEVFEED_PARTNER_BASE_URL", "https://partner.example");
  const fetcher = vi.fn().mockImplementation(() => Response.json({ ok: true }));
  vi.stubGlobal("fetch", fetcher);
  const segments = [...accountConnections, ...suffix];
  for (const method of ["GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"]) {
    fetcher.mockClear();
    const write = !["GET", "HEAD", "OPTIONS"].includes(method);
    const payload = { expected_revision: 3, credential: "fixture-only-write-value" };
    const response = await gateway(
      new Request("https://partner.example/api?limit=10&offset=25", {
        method,
        headers: {
          origin: "https://partner.example",
          cookie: "partner_session=private",
          "x-csrf-token": "partner-csrf",
          "content-type": "application/json",
          authorization: "never-forward",
        },
        ...(write ? { body: JSON.stringify(payload) } : {}),
      }),
      segments,
    );
    expect(response.status).toBe(allowed.includes(method) ? 200 : 405);
    if (!allowed.includes(method)) {
      expect(fetcher).not.toHaveBeenCalled();
      continue;
    }
    expect(fetcher).toHaveBeenCalledOnce();
    const [url, init] = fetcher.mock.calls[0];
    expect(url).toBe(`http://partner-api:8004/${segments.join("/")}?limit=10&offset=25`);
    expect(init.method).toBe(method);
    expect(init.headers.get("origin")).toBe("https://partner.example");
    expect(init.headers.get("x-csrf-token")).toBe("partner-csrf");
    expect(init.headers.get("cookie")).toBe("partner_session=private");
    expect(init.headers.has("authorization")).toBe(false);
    expect(init.redirect).toBe("manual");
    expect(init.cache).toBe("no-store");
    if (write) expect(JSON.parse(new TextDecoder().decode(init.body))).toEqual(payload);
    else expect(init.body).toBeUndefined();
  }
});

it("rejects malformed connection scopes and nested routes before contacting the API", async () => {
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  for (const segments of [
    [...accountConnections.slice(0, 3), "not-a-uuid", "connections"],
    ["v1", "partner", "connections", "source", "actions"],
    [...accountConnections, "source_name"],
    [...accountConnections, "source--name"],
    [...accountConnections, "source-"],
    [...accountConnections, "Source"],
    [...accountConnections, "new"],
    [...accountConnections, "a".repeat(201)],
    [...accountConnections, "source", "unknown"],
    [...accountConnections, "source", "jobs", "extra"],
    [...accountConnections, "source", "actions", "extra"],
    [...accountConnections, "", "actions"],
    [...accountConnections.slice(0, -1), "Connections"],
    [...accountConnections.slice(0, -1), "connections/source"],
  ]) {
    for (const method of ["GET", "POST"]) {
      expect(
        (await gateway(new Request("https://partner.example/api", { method }), segments)).status,
      ).toBe(404);
    }
  }
  expect(fetcher).not.toHaveBeenCalled();
});

it.each([
  { method: "POST", suffix: [] },
  { method: "PUT", suffix: ["source"] },
  { method: "DELETE", suffix: ["source"] },
  { method: "POST", suffix: ["source", "actions"] },
])(
  "origin-checks connection $method $suffix before reading or forwarding the body",
  async ({ method, suffix }) => {
    vi.stubEnv("DEVFEED_PARTNER_BASE_URL", "https://partner.example");
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    for (const origin of [undefined, "https://evil.example"]) {
      const response = await gateway(
        new Request("https://partner.example/api", {
          method,
          headers: origin
            ? { origin, "x-csrf-token": "partner-csrf" }
            : { "x-csrf-token": "partner-csrf" },
          body: "{}",
        }),
        [...accountConnections, ...suffix],
      );
      expect(response.status).toBe(403);
      expect(await response.json()).toEqual({ detail: "Invalid request origin" });
    }
    expect(fetcher).not.toHaveBeenCalled();
  },
);

it("bounds incomplete connection creation uploads with the shared body deadline", async () => {
  vi.useFakeTimers();
  vi.stubEnv("DEVFEED_PARTNER_BASE_URL", "https://partner.example");
  const fetcher = vi.fn();
  const cancel = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  const incoming = new Request("https://partner.example/api", {
    method: "POST",
    headers: { origin: "https://partner.example", "x-csrf-token": "csrf" },
    body: new ReadableStream<Uint8Array>({ cancel }),
    duplex: "half",
  } as RequestInit);
  const pending = gateway(incoming, accountConnections);
  await vi.advanceTimersByTimeAsync(2000);
  const response = await pending;
  expect(response.status).toBe(408);
  expect(cancel).toHaveBeenCalledOnce();
  expect(fetcher).not.toHaveBeenCalled();
  expect(vi.getTimerCount()).toBe(0);
});

it("rejects oversized connection mutation bodies before forwarding credentials", async () => {
  vi.stubEnv("DEVFEED_PARTNER_BASE_URL", "https://partner.example");
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  const response = await gateway(
    new Request("https://partner.example/api", {
      method: "PUT",
      headers: { origin: "https://partner.example", "content-length": "1000001" },
      body: "{}",
    }),
    [...accountConnections, "source"],
  );
  expect(response.status).toBe(413);
  expect(fetcher).not.toHaveBeenCalled();
});

it.each([
  ["connector-response"],
  ["connector-preview"],
  ["catalog-connection", "connector-response"],
  ["catalog-connection", "connector-preview"],
])("proxies only account-scoped preview POST requests: %j", async (...suffix) => {
  vi.stubEnv("DEVFEED_PARTNER_BASE_URL", "https://partner.example");
  const fetcher = vi
    .fn()
    .mockImplementation(async () => Response.json({ data: {}, sampled: false }));
  vi.stubGlobal("fetch", fetcher);
  const path = [
    "v1",
    "partner",
    "accounts",
    "11111111-1111-4111-8111-111111111111",
    "connections",
    ...suffix,
  ];
  const request = (method = "POST", origin = "https://partner.example") =>
    new Request("https://partner.example/api", {
      method,
      headers: { origin, "x-csrf-token": "preview-csrf", "content-type": "application/json" },
      ...(!["GET", "HEAD"].includes(method)
        ? { body: JSON.stringify({ connector: { base_url: "https://catalog.example" } }) }
        : {}),
    });
  expect((await gateway(request(), path)).status).toBe(200);
  expect(fetcher.mock.calls[0][0]).toBe(`http://partner-api:8004/${path.join("/")}`);
  expect(fetcher.mock.calls[0][1].headers.get("x-csrf-token")).toBe("preview-csrf");
  fetcher.mockClear();
  expect((await gateway(request("POST", "https://foreign.example"), path)).status).toBe(403);
  for (const method of ["GET", "PUT", "DELETE", "PATCH"]) {
    expect((await gateway(request(method), path)).status).toBe(405);
  }
  for (const invalid of [
    [...path, "extra"],
    ["v1", "partner", "connections", ...suffix],
    ["v1", "partner", "accounts", "not-a-uuid", "connections", ...suffix],
  ]) {
    expect([404, 405]).toContain((await gateway(request(), invalid)).status);
  }
  expect(fetcher).not.toHaveBeenCalled();
});
