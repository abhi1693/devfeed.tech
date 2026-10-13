// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { PartnerConnections, connectionPath } from "../src/app/partner-connections";
import {
  PartnerConnectionForm,
  type ConnectionFormInput,
} from "../src/app/partner-connection-form";
import { partnerAccount } from "./partner-fixtures";
import type { WorkspaceConnectionOut } from "../src/lib/api/generated/models";

const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => router }));
const account = partnerAccount();
const unavailable = vi.fn();

function connection(overrides: Partial<WorkspaceConnectionOut> = {}): WorkspaceConnectionOut {
  return {
    provider: "partner-connection-1",
    account_id: account.id,
    account_name: account.name,
    credentials_available: true,
    name: "Product catalog",
    api_url: "https://catalog.example/products",
    connector: {
      base_url: "https://catalog.example",
      list_path: "/products",
      auth: { mode: "none" },
    },
    enabled: true,
    sync_interval_minutes: 360,
    revision: 1,
    state: "idle",
    last_sync_at: null,
    next_sync_at: null,
    error: null,
    products: 12,
    qualified: 10,
    commercial_approved: 10,
    checking: 2,
    needs_attention: 0,
    excluded: 0,
    ai_enabled: true,
    auth_mode: "none",
    credential_header: "X-API-Key",
    credential_configured: false,
    credentials_managed: false,
    connector_editable: true,
    ...overrides,
  };
}

function list(items: WorkspaceConnectionOut[] = [], extra = {}) {
  return {
    items,
    total: items.length,
    offset: 0,
    limit: 25,
    credentials_available: true,
    ...extra,
  };
}

function form(existing?: WorkspaceConnectionOut, extra = {}) {
  const save = vi.fn<(input: ConnectionFormInput) => Promise<void>>(async () => {});
  render(
    <PartnerConnectionForm
      connection={existing}
      credentialsAvailable
      cancelHref="/connections"
      saving={false}
      error=""
      onUnavailable={unavailable}
      onSave={save}
      {...extra}
    />,
  );
  return save;
}

async function fillNew() {
  fireEvent.click(screen.getByRole("combobox", { name: "Partner account" }));
  fireEvent.click(await screen.findByRole("option", { name: account.name }));
  fireEvent.change(screen.getByRole("textbox", { name: /Connection name/ }), {
    target: { value: "My catalog" },
  });
  fireEvent.change(screen.getByRole("textbox", { name: /API base URL/ }), {
    target: { value: "https://catalog.example" },
  });
}

beforeEach(() => vi.clearAllMocks());
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("uses linked compact records and real create/edit URLs without modal dialogs", async () => {
  const item = connection();
  vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(Response.json(list([item]))));
  render(<PartnerConnections csrfToken="csrf" onUnavailable={unavailable} />);
  const name = await screen.findByRole("link", { name: item.name });
  expect(name.getAttribute("href")).toBe(connectionPath(item.provider));
  expect(screen.getByRole("link", { name: "Create connection" }).getAttribute("href")).toBe(
    `/connections/new`,
  );
  expect(screen.getByRole("link", { name: `Edit ${item.name}` }).getAttribute("href")).toMatch(
    /\/edit$/,
  );
  expect(screen.queryByRole("dialog")).toBeNull();
});

it("lists connections across authorized accounts without choosing a global account", async () => {
  const other = connection({
    provider: "other-catalog",
    name: "Build catalog",
    account_id: "22222222-2222-4222-8222-222222222222",
    account_name: "Build Studio",
  });
  const fetcher = vi
    .fn<typeof fetch>()
    .mockResolvedValue(Response.json(list([connection(), other])));
  vi.stubGlobal("fetch", fetcher);
  render(<PartnerConnections csrfToken="csrf" onUnavailable={unavailable} />);
  await screen.findByRole("link", { name: "Build catalog" });
  expect(screen.getByRole("columnheader", { name: "Account" })).toBeTruthy();
  expect(screen.getByRole("link", { name: "Build Studio" }).getAttribute("href")).toBe(
    `/settings/${other.account_id}`,
  );
  expect(
    screen.getByRole("link", { name: "Filter connections by Build Studio" }).getAttribute("href"),
  ).toContain(`account_id=${other.account_id}`);
  expect(screen.queryByRole("combobox", { name: "Partner account" })).toBeNull();
  expect(fetcher.mock.calls[0][0]).toBe("/api/v1/partner/connections?limit=25&offset=0&q=");
});

it("resolves bookmarked global details and sends mutations to that record's actual owner", async () => {
  const other = connection({
    provider: "other-catalog",
    account_id: "22222222-2222-4222-8222-222222222222",
    account_name: "Build Studio",
  });
  const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input, init) =>
    String(input).includes("/jobs?")
      ? Response.json({ items: [], total: 0, limit: 10, offset: 0 })
      : Response.json({
          ...other,
          ...(init?.method === "POST" ? { enabled: false, revision: 2 } : {}),
        }),
  );
  vi.stubGlobal("fetch", fetcher);
  render(
    <PartnerConnections
      csrfToken="csrf"
      onUnavailable={unavailable}
      route={{ view: "detail", connectionId: other.provider }}
    />,
  );
  await screen.findByRole("link", { name: "Build Studio" });
  fireEvent.click(screen.getByRole("button", { name: "Pause imports" }));
  await screen.findByRole("button", { name: "Resume imports" });
  const write = fetcher.mock.calls.find(([, init]) => init?.method === "POST")!;
  expect(write[0]).toBe(
    `/api/v1/partner/accounts/${other.account_id}/connections/${other.provider}/actions`,
  );
  expect(JSON.parse(String(write[1]!.body))).toEqual({ action: "pause", expected_revision: 1 });
});

it("requests only the bookmarked bounded connection page", async () => {
  const fetcher = vi
    .fn<typeof fetch>()
    .mockResolvedValue(Response.json(list([connection()], { total: 101, offset: 25 })));
  vi.stubGlobal("fetch", fetcher);
  render(
    <PartnerConnections
      csrfToken="csrf"
      onUnavailable={unavailable}
      route={{ view: "list", offset: 25 }}
    />,
  );
  await screen.findByRole("link", { name: "Product catalog" });
  expect(fetcher.mock.calls[0][0]).toBe(`/api/v1/partner/connections?limit=25&offset=25&q=`);
  expect(screen.getByRole("link", { name: "Next" }).getAttribute("href")).toContain(
    "connection_offset=50",
  );
});

it("rejects a filtered catalog page containing another account's connection", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn<typeof fetch>()
      .mockResolvedValue(
        Response.json(list([connection({ account_id: "22222222-2222-4222-8222-222222222222" })])),
      ),
  );
  render(
    <PartnerConnections filterAccount={account} csrfToken="csrf" onUnavailable={unavailable} />,
  );
  expect((await screen.findByRole("alert")).textContent).toContain(
    "Could not load import connections",
  );
  expect(screen.queryByRole("link", { name: "Product catalog" })).toBeNull();
});

it("creates a structured public API connection with write protection and navigates to its record", async () => {
  const fetcher = vi
    .fn<typeof fetch>()
    .mockImplementation(async (url, init) =>
      init?.method === "POST"
        ? Response.json(connection({ name: "My catalog" }))
        : Response.json(
            String(url).includes("/accounts?") ? { items: [account], total: 1 } : list(),
          ),
    );
  vi.stubGlobal("fetch", fetcher);
  render(
    <PartnerConnections
      csrfToken="csrf-value"
      onUnavailable={unavailable}
      route={{ view: "create" }}
    />,
  );
  await screen.findByRole("textbox", { name: /Connection name/ });
  await fillNew();
  fireEvent.click(screen.getByRole("button", { name: "Create connection" }));
  await waitFor(() =>
    expect(router.push).toHaveBeenCalledWith(connectionPath("partner-connection-1")),
  );
  const init = fetcher.mock.calls.find(([, options]) => options?.method === "POST")![1]!;
  expect(init.headers).toMatchObject({ "x-csrf-token": "csrf-value" });
  expect(JSON.parse(String(init.body))).toMatchObject({
    name: "My catalog",
    connector: {
      base_url: "https://catalog.example",
      auth: { mode: "none", source: "connection", secret_ref: "" },
      max_pages: 100,
    },
    enabled: true,
  });
});

it("keeps the draft and gives refresh instructions after a revision conflict", async () => {
  const existing = connection();
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>().mockImplementation(async (url, init) => {
      if (init?.method === "PUT")
        return Response.json({ detail: "private worker details" }, { status: 409 });
      return Response.json(String(url).includes("?limit=") ? list([existing]) : existing);
    }),
  );
  render(
    <PartnerConnections
      csrfToken="csrf"
      onUnavailable={unavailable}
      route={{ view: "edit", connectionId: existing.provider }}
    />,
  );
  const name = await screen.findByRole("textbox", { name: /Connection name/ });
  fireEvent.change(name, { target: { value: "Unsaved catalog" } });
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  expect((await screen.findByRole("alert")).textContent).toContain(
    "Refresh the page before saving again",
  );
  expect((name as HTMLInputElement).value).toBe("Unsaved catalog");
  expect(router.push).not.toHaveBeenCalled();
  expect(unavailable).not.toHaveBeenCalled();
});

it("does not publish success for a save returning the wrong connection", async () => {
  const existing = connection();
  vi.stubGlobal(
    "fetch",
    vi
      .fn<typeof fetch>()
      .mockImplementation(async (url, init) =>
        Response.json(
          init?.method === "PUT"
            ? connection({ provider: "different-record" })
            : String(url).includes("?limit=")
              ? list([existing])
              : existing,
        ),
      ),
  );
  render(
    <PartnerConnections
      csrfToken="csrf"
      onUnavailable={unavailable}
      route={{ view: "edit", connectionId: existing.provider }}
    />,
  );
  await screen.findByRole("textbox", { name: /Connection name/ });
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  expect((await screen.findByRole("alert")).textContent).toContain(
    "Could not confirm the saved import connection",
  );
  expect(router.push).not.toHaveBeenCalled();
});

it("shows a missing connection inline without revoking account access", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn<typeof fetch>()
      .mockImplementation(async (url) =>
        String(url).includes("?limit=")
          ? Response.json(list())
          : new Response(null, { status: 404 }),
      ),
  );
  render(
    <PartnerConnections
      csrfToken="csrf"
      onUnavailable={unavailable}
      route={{ view: "edit", connectionId: "missing" }}
    />,
  );
  expect((await screen.findByRole("alert")).textContent).toContain(
    "Could not load this import connection",
  );
  expect(unavailable).not.toHaveBeenCalled();
  expect(router.replace).not.toHaveBeenCalled();
});

it("disconnects from an explicit page and returns to the collection", async () => {
  const existing = connection();
  const fetcher = vi
    .fn<typeof fetch>()
    .mockImplementation(async (_url, init) =>
      Response.json(
        init?.method === "DELETE"
          ? connection({ enabled: false, state: "disconnected" })
          : existing,
      ),
    );
  vi.stubGlobal("fetch", fetcher);
  render(
    <PartnerConnections
      csrfToken="csrf"
      onUnavailable={unavailable}
      route={{ view: "disconnect", connectionId: existing.provider }}
    />,
  );
  await screen.findByText(/Your imported products and historical performance remain available/);
  expect(screen.queryByRole("dialog")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Disconnect connection" }));
  await waitFor(() => expect(router.push).toHaveBeenCalledWith(connectionPath()));
  expect(
    JSON.parse(String(fetcher.mock.calls.find(([, init]) => init?.method === "DELETE")![1]!.body)),
  ).toEqual({ expected_revision: 1 });
});

it("never pre-fills a credential and omits unchanged credentials and connector limits", async () => {
  const save = form(
    connection({
      credential_configured: true,
      auth_mode: "bearer",
      connector: {
        base_url: "https://catalog.example",
        max_pages: 1000,
        auth: { mode: "bearer", source: "connection", secret_ref: "" },
      },
    }),
  );
  expect(screen.queryByLabelText(/^Bearer token/)).toBeNull();
  fireEvent.change(screen.getByRole("textbox", { name: /Connection name/ }), {
    target: { value: "Renamed" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() =>
    expect(save).toHaveBeenCalledExactlyOnceWith({ name: "Renamed", enabled: true }),
  );
});

it("uses explicit write-only replacement and supports removing authentication", async () => {
  const save = form(
    connection({
      credential_configured: true,
      auth_mode: "bearer",
      connector: {
        base_url: "https://catalog.example",
        auth: { mode: "bearer", source: "connection", secret_ref: "" },
      },
    }),
  );
  fireEvent.click(screen.getByRole("checkbox", { name: "Replace credential" }));
  const token = screen.getByLabelText(/^Bearer token/) as HTMLInputElement;
  expect(token.type).toBe("password");
  expect(token.value).toBe("");
  fireEvent.change(token, { target: { value: "fixture-only-secret" } });
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() =>
    expect(save).toHaveBeenCalledWith({
      name: "Product catalog",
      enabled: true,
      credential: "fixture-only-secret",
    }),
  );
  fireEvent.change(screen.getByRole("combobox", { name: "Authentication method" }), {
    target: { value: "none" },
  });
  expect(screen.getByRole("status").textContent).toContain("removes the stored credential");
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() =>
    expect(save).toHaveBeenLastCalledWith(
      expect.objectContaining({
        connector: expect.objectContaining({ auth: expect.objectContaining({ mode: "none" }) }),
      }),
    ),
  );
  expect(save.mock.calls.at(-1)![0]).not.toHaveProperty("credential");
});

it("requires a fresh credential when changing an authenticated API origin", async () => {
  const save = form(
    connection({
      credential_configured: true,
      auth_mode: "bearer",
      connector: {
        base_url: "https://catalog.example",
        auth: { mode: "bearer", source: "connection" },
      },
    }),
  );
  fireEvent.change(screen.getByRole("textbox", { name: /API base URL/ }), {
    target: { value: "https://new-catalog.example" },
  });
  expect((screen.getByLabelText(/^Bearer token/) as HTMLInputElement).value).toBe("");
  fireEvent.submit(screen.getByRole("button", { name: "Save changes" }).closest("form")!);
  expect(screen.getByRole("alert").textContent).toContain("Enter the credential");
  expect(save).not.toHaveBeenCalled();
});

it("keeps managed API origins and credentials outside editable controls", async () => {
  const save = form(
    connection({
      connector: null,
      credentials_managed: true,
      connector_editable: false,
      credential_configured: true,
      auth_mode: "bearer",
    }),
  );
  expect(screen.queryByRole("textbox", { name: /API base URL/ })).toBeNull();
  expect(screen.queryByRole("combobox", { name: "Authentication method" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() =>
    expect(save).toHaveBeenCalledExactlyOnceWith({ name: "Product catalog", enabled: true }),
  );
});

it("truthfully disables authenticated creation without server credential storage", () => {
  form(undefined, { credentialsAvailable: false });
  expect((screen.getByRole("option", { name: "Bearer token" }) as HTMLOptionElement).disabled).toBe(
    true,
  );
  expect(screen.getByText(/Authenticated imports are not configured/)).toBeTruthy();
});

it("preserves an administrator's existing interval until it is explicitly changed", async () => {
  const save = form(connection({ sync_interval_minutes: 1 }));
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() =>
    expect(save).toHaveBeenCalledExactlyOnceWith({ name: "Product catalog", enabled: true }),
  );
  fireEvent.change(screen.getByRole("spinbutton", { name: /Sync interval/ }), {
    target: { value: "2" },
  });
  fireEvent.submit(screen.getByRole("button", { name: "Save changes" }).closest("form")!);
  expect(screen.getByRole("alert").textContent).toContain("between 15 and 10,080");
  expect(save).toHaveBeenCalledTimes(1);
});
