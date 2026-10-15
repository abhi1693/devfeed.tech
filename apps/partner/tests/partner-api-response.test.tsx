// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { PartnerApiResponse } from "../src/app/partner-api-response";
import type { ConnectorConfig, PartnerConnectionOut } from "../src/lib/api/generated/models";
import type { ResponseMapping } from "@devfeed/ui/connector-response-mappings";

const router = vi.hoisted(() => ({ replace: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => router }));

const ACCOUNT = "11111111-1111-4111-8111-111111111111";
const OTHER_ACCOUNT = "22222222-2222-4222-8222-222222222222";
const connector: ConnectorConfig = {
  base_url: "https://catalog.example.com",
  list_path: "/products",
  items_paths: ["items"],
  fields: { name: ["name"] },
  auth: { mode: "bearer" },
  pagination: { mode: "cursor", parameter: "after", next_path: "next" },
};
const connection: PartnerConnectionOut = {
  account_id: ACCOUNT,
  provider: "catalog:launch/products",
  name: "Catalog",
  enabled: true,
  ai_enabled: false,
  api_url: connector.base_url,
  connector,
  credential_configured: true,
  credential_header: "",
  auth_mode: "bearer",
  products: 1,
  qualified: 1,
  checking: 0,
  excluded: 0,
  needs_attention: 0,
  last_sync_at: null,
  next_sync_at: null,
  error: null,
  state: "idle",
  revision: 9,
  sync_interval_minutes: 360,
};
const sample = { items: [{ id: "product-1", name: "Useful product" }], next: "page-2" };
const mappings: ResponseMapping[] = [
  {
    field: "fields.name",
    label: "Product name",
    style: "border-violet-600",
    addresses: [JSON.stringify(["items", "0", "name"])],
  },
];
const unavailable = vi.fn();
const onResponse = vi.fn();
const props = {
  accountId: ACCOUNT,
  connector,
  credential: "write-only-api-token",
  csrfToken: "csrf-preview-token",
  onUnavailable: unavailable,
  onResponse,
  mappings,
  credentialReady: true,
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

async function advance(milliseconds = 700) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(milliseconds);
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

it("debounces a draft response with scoped authorization, CSRF and a write-only credential", async () => {
  const fetcher = vi
    .fn<typeof fetch>()
    .mockResolvedValue(Response.json({ data: sample, sampled: true }));
  vi.stubGlobal("fetch", fetcher);
  render(<PartnerApiResponse {...props} />);
  await advance(699);
  expect(fetcher).not.toHaveBeenCalled();
  await advance(1);
  expect(fetcher).toHaveBeenCalledOnce();
  const [url, init] = fetcher.mock.calls[0];
  expect(url).toBe(`/api/v1/partner/accounts/${ACCOUNT}/connections/connector-response`);
  expect(init).toMatchObject({
    method: "POST",
    cache: "no-store",
    credentials: "same-origin",
    headers: { "content-type": "application/json", "x-csrf-token": props.csrfToken },
    signal: expect.any(AbortSignal),
  });
  const body = JSON.parse(init!.body as string);
  expect(body.credential).toBe(props.credential);
  expect(body).not.toHaveProperty("expected_revision");
  expect(body.connector).toMatchObject({
    base_url: connector.base_url,
    list_path: "/products",
    auth: connector.auth,
    pagination: { mode: "cursor", parameter: "after" },
  });
  expect(body.connector).not.toHaveProperty("fields");
  expect(body.connector.pagination).not.toHaveProperty("next_path");
  expect(screen.getByRole("textbox", { name: "API response JSON" }).textContent).toContain(
    "Useful product",
  );
  expect(screen.getByText(/Sample response/)).toBeTruthy();
  expect(document.body.textContent).not.toContain(props.credential);
  expect(onResponse).toHaveBeenLastCalledWith(sample);
});

it("previews an existing revision with the stored credential without reading it into the form", async () => {
  const fetcher = vi
    .fn<typeof fetch>()
    .mockResolvedValue(Response.json({ data: null, sampled: false }));
  vi.stubGlobal("fetch", fetcher);
  render(<PartnerApiResponse {...props} connection={connection} credential={undefined} />);
  await advance();
  const [url, init] = fetcher.mock.calls[0];
  expect(url).toBe(
    `/api/v1/partner/accounts/${ACCOUNT}/connections/catalog%3Alaunch%2Fproducts/connector-response`,
  );
  const body = JSON.parse(init!.body as string);
  expect(body.expected_revision).toBe(9);
  expect(body).not.toHaveProperty("credential");
  expect(screen.getByRole("textbox", { name: "API response JSON" }).textContent).toBe("null\n");
  expect(onResponse).toHaveBeenLastCalledWith(null);
});

it.each([
  { accountId: undefined, credentialReady: true, message: /Choose a partner account/ },
  { accountId: ACCOUNT, credentialReady: false, message: /Enter a credential/ },
])(
  "does not send requests before account and credential requirements are met ($message)",
  async (caseProps) => {
    const fetcher = vi.fn<typeof fetch>();
    vi.stubGlobal("fetch", fetcher);
    render(<PartnerApiResponse {...props} {...caseProps} />);
    await advance(2000);
    expect(fetcher).not.toHaveBeenCalled();
    expect(screen.getByText(caseProps.message)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Refresh" }).hasAttribute("disabled")).toBe(true);
  },
);

it.each([
  "http://catalog.example.com",
  "https://user:token@catalog.example.com",
  "https://catalog.example.com/path",
])("does not preview an invalid or credential-bearing API origin %s", async (base_url) => {
  const fetcher = vi.fn<typeof fetch>();
  vi.stubGlobal("fetch", fetcher);
  render(<PartnerApiResponse {...props} connector={{ ...connector, base_url }} />);
  await advance();
  expect(fetcher).not.toHaveBeenCalled();
  expect(screen.getByText(/Enter the API base URL/)).toBeTruthy();
});

it("recolors the existing sample without refetching for mappings, list paths, filters or response pagination paths", async () => {
  const fetcher = vi
    .fn<typeof fetch>()
    .mockResolvedValue(Response.json({ data: sample, sampled: false }));
  vi.stubGlobal("fetch", fetcher);
  const view = render(<PartnerApiResponse {...props} />);
  await advance();
  expect(document.querySelector('[data-mappings="fields.name"]')?.textContent).toContain('"name"');
  view.rerender(
    <PartnerApiResponse
      {...props}
      connector={{
        ...connector,
        items_paths: ["data.products"],
        fields: { name: ["id"] },
        filters: [{ path: "name", values: ["Useful product"] }],
        pagination: { ...connector.pagination, next_path: "data.next", total_path: "total" },
      }}
      mappings={[{ ...mappings[0], addresses: [JSON.stringify(["items", "0", "id"])] }]}
    />,
  );
  await advance(2000);
  expect(fetcher).toHaveBeenCalledOnce();
  expect(document.querySelector('[data-mappings="fields.name"]')?.textContent).toContain('"id"');
  expect(screen.getByRole("textbox", { name: "API response JSON" }).textContent).toContain(
    "Useful product",
  );
});

it.each(["fetch", "json"] as const)(
  "aborts stale %s replies when the requested account changes",
  async (phase) => {
    const oldResponse = deferred<Response>();
    const oldBody = deferred<unknown>();
    const oldJson = vi.fn(() => oldBody.promise);
    const fetcher = vi
      .fn<typeof fetch>()
      .mockReturnValueOnce(
        phase === "fetch"
          ? oldResponse.promise
          : Promise.resolve({ status: 200, ok: true, json: oldJson } as unknown as Response),
      )
      .mockResolvedValueOnce(Response.json({ data: { new_account: true }, sampled: false }));
    vi.stubGlobal("fetch", fetcher);
    const view = render(<PartnerApiResponse {...props} />);
    await advance();
    view.rerender(<PartnerApiResponse {...props} accountId={OTHER_ACCOUNT} />);
    expect(fetcher.mock.calls[0][1]!.signal!.aborted).toBe(true);
    expect(onResponse).toHaveBeenLastCalledWith(undefined);
    await advance();
    await act(async () => {
      oldResponse.resolve(new Response(null, { status: 401 }));
      oldBody.resolve({ data: { old_account: true }, sampled: false });
    });
    expect(screen.getByRole("textbox", { name: "API response JSON" }).textContent).toContain(
      "new_account",
    );
    expect(document.body.textContent).not.toContain("old_account");
    expect(unavailable).not.toHaveBeenCalled();
    expect(router.replace).not.toHaveBeenCalled();
  },
);

it("clears old samples immediately on credential changes and refetches only the final request settings", async () => {
  const fetcher = vi
    .fn<typeof fetch>()
    .mockResolvedValue(Response.json({ data: sample, sampled: false }));
  vi.stubGlobal("fetch", fetcher);
  const view = render(<PartnerApiResponse {...props} />);
  await advance();
  view.rerender(<PartnerApiResponse {...props} credential="replacement-token" />);
  expect(screen.queryByRole("textbox", { name: "API response JSON" })).toBeNull();
  expect(onResponse).toHaveBeenLastCalledWith(undefined);
  await advance(300);
  view.rerender(
    <PartnerApiResponse
      {...props}
      credential="replacement-token"
      connector={{
        ...connector,
        parameters: { published: true },
        pagination: { mode: "page", start: 2 },
      }}
    />,
  );
  await advance(699);
  expect(fetcher).toHaveBeenCalledOnce();
  await advance(1);
  expect(fetcher).toHaveBeenCalledTimes(2);
  expect(JSON.parse(fetcher.mock.calls[1][1]!.body as string)).toMatchObject({
    credential: "replacement-token",
    connector: { parameters: { published: true }, pagination: { mode: "page", start: 2 } },
  });
  expect(document.body.textContent).not.toContain("replacement-token");
});

it("keeps a completed sample while saving or testing and permits an explicit fresh response afterward", async () => {
  const fetcher = vi
    .fn<typeof fetch>()
    .mockResolvedValue(Response.json({ data: sample, sampled: false }));
  vi.stubGlobal("fetch", fetcher);
  const view = render(<PartnerApiResponse {...props} />);
  await advance();
  view.rerender(<PartnerApiResponse {...props} disabled />);
  await advance();
  expect(screen.getByRole("textbox", { name: "API response JSON" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Refresh" }).hasAttribute("disabled")).toBe(true);
  view.rerender(<PartnerApiResponse {...props} disabled={false} />);
  await advance();
  expect(fetcher).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
  await advance();
  expect(fetcher).toHaveBeenCalledTimes(2);
});

it("delegates session loss to the shared partner request handler", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>().mockResolvedValue(new Response(null, { status: 401 })),
  );
  render(<PartnerApiResponse {...props} />);
  await advance();
  expect(unavailable).toHaveBeenCalledOnce();
  expect(router.replace).toHaveBeenCalledExactlyOnceWith("/login");
  expect(screen.queryByRole("alert")).toBeNull();
});

it.each([
  [409, /This connection changed/],
  [422, /Check the endpoint and authentication/],
  [429, /Too many previews/],
])(
  "shows safe recovery instructions after HTTP %s without echoing upstream secrets",
  async (status, message) => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn<typeof fetch>()
        .mockResolvedValue(Response.json({ detail: props.credential }, { status })),
    );
    render(<PartnerApiResponse {...props} />);
    await advance();
    expect(screen.getByRole("alert").textContent).toMatch(message);
    expect(document.body.textContent).not.toContain(props.credential);
    expect(unavailable).not.toHaveBeenCalled();
  },
);

it.each([{ data: sample, sampled: "yes" }, { sampled: false }])(
  "rejects an invalid response contract and cancels requests when unmounted (%j)",
  async (invalid) => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(Response.json(invalid));
    vi.stubGlobal("fetch", fetcher);
    const view = render(<PartnerApiResponse {...props} />);
    await advance();
    expect(screen.getByRole("alert").textContent).toMatch(/Could not confirm/);
    const pending = deferred<Response>();
    fetcher.mockReturnValueOnce(pending.promise);
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await advance();
    const signal = fetcher.mock.calls[1][1]!.signal!;
    view.unmount();
    expect(signal.aborted).toBe(true);
    await act(async () => pending.resolve(new Response(null, { status: 401 })));
    expect(unavailable).not.toHaveBeenCalled();
    expect(router.replace).not.toHaveBeenCalled();
  },
);
