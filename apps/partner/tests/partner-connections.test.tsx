// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
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
    sync_interval_minutes: 1440,
    account_tier: "gold",
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
      csrfToken="csrf"
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
  expect(screen.queryByRole("link", { name: /^Filter connections by/ })).toBeNull();
  expect(screen.queryByRole("link", { name: "Clear account filter" })).toBeNull();
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

it("keeps connection search and pagination across every authorized account", async () => {
  const fetcher = vi
    .fn<typeof fetch>()
    .mockResolvedValue(Response.json(list([connection()], { total: 101, offset: 25 })));
  vi.stubGlobal("fetch", fetcher);
  render(
    <PartnerConnections
      csrfToken="csrf"
      onUnavailable={unavailable}
      route={{ view: "list", q: "catalog", offset: 25 }}
    />,
  );
  await screen.findByRole("link", { name: "Product catalog" });
  expect(fetcher.mock.calls[0][0]).toBe("/api/v1/partner/connections?limit=25&offset=25&q=catalog");
  expect(screen.getByRole("link", { name: "Next" }).getAttribute("href")).toBe(
    "/connections?connection_offset=50&q=catalog",
  );
  fireEvent.change(screen.getByRole("textbox", { name: "Search connections" }), {
    target: { value: " Build Studio " },
  });
  fireEvent.click(screen.getByRole("button", { name: "Search" }));
  expect(router.push).toHaveBeenCalledWith("/connections?q=Build+Studio");
  expect(screen.queryByRole("link", { name: /^Filter connections by/ })).toBeNull();
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
      auth: { mode: "none" },
      max_pages: 100,
    },
    enabled: true,
  });
});

it("imports a logo URL through the normal connection save", async () => {
  const save = form(connection());
  fireEvent.change(screen.getByRole("textbox", { name: "Logo URL" }), {
    target: { value: "https://platform.example/logo.png" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() =>
    expect(save).toHaveBeenCalledWith(
      expect.objectContaining({ logo_url: "https://platform.example/logo.png" }),
    ),
  );
});

it("updates a saved logo immediately, preserves other draft edits and saves with its latest revision", async () => {
  const existing = connection({ logo_url: null, logo_variants: [] });
  let revision = 1;
  const fetcher = vi.fn<typeof fetch>().mockImplementation(async (url, init) => {
    if (String(url).endsWith("/logo")) {
      revision++;
      const removing = init?.method === "PUT" && JSON.parse(String(init.body)).logo_url === null;
      return Response.json({
        ...existing,
        revision,
        logo_url: removing ? null : "https://cdn.example/logo.webp",
        logo_variants: removing ? [] : [{ url: "https://cdn.example/logo.webp", width: 64 }],
      });
    }
    if (init?.method === "PUT")
      return Response.json({ ...existing, name: "Draft catalog name", revision: 5 });
    return Response.json(existing);
  });
  vi.stubGlobal("fetch", fetcher);
  render(
    <PartnerConnections
      csrfToken="csrf"
      onUnavailable={unavailable}
      route={{ view: "edit", connectionId: existing.provider }}
    />,
  );
  const name = (await screen.findByRole("textbox", {
    name: /Connection name/,
  })) as HTMLInputElement;
  fireEvent.change(name, { target: { value: "Draft catalog name" } });
  const api = screen.getByRole("textbox", { name: /API base URL/ }) as HTMLInputElement;
  fireEvent.change(api, { target: { value: "https://draft-api.example" } });
  const input = screen.getByRole("textbox", { name: "Logo URL" }) as HTMLInputElement;
  fireEvent.change(input, { target: { value: "https://remote.example/logo.png" } });
  fireEvent.blur(input);
  await waitFor(() => expect(input.value).toBe("https://cdn.example/logo.webp"));
  expect(name.value).toBe("Draft catalog name");
  expect(api.value).toBe("https://draft-api.example");
  fireEvent.click(screen.getByRole("button", { name: "Remove logo" }));
  await screen.findByText("Logo removed.");
  const file = new File(["image"], "platform.png", { type: "image/png" });
  fireEvent.change(screen.getByLabelText("Logo image file"), { target: { files: [file] } });
  await screen.findByText("Logo updated.");
  expect(name.value).toBe("Draft catalog name");
  expect(api.value).toBe("https://draft-api.example");
  expect(
    fetcher.mock.calls.filter(
      ([url, init]) => init?.method === "PUT" && !String(url).endsWith("/logo"),
    ),
  ).toHaveLength(0);
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(router.push).toHaveBeenCalledWith(connectionPath(existing.provider)));
  const write = fetcher.mock.calls.find(
    ([url, init]) => init?.method === "PUT" && !String(url).endsWith("/logo"),
  )!;
  const payload = JSON.parse(String(write[1]!.body));
  expect(payload).toMatchObject({
    name: "Draft catalog name",
    expected_revision: 4,
    connector: { base_url: "https://draft-api.example" },
  });
  expect(payload).not.toHaveProperty("logo_url");
  expect(payload).not.toHaveProperty("logo_file");
});

it("saves a typed logo URL and name together on the first Save click without a competing logo request", async () => {
  const user = userEvent.setup();
  const existing = connection();
  const fetcher = vi.fn<typeof fetch>().mockImplementation(async (_url, init) =>
    Response.json(
      init?.method === "PUT"
        ? {
            ...existing,
            name: "Edited catalog",
            revision: 2,
            logo_url: "https://cdn.example/saved.webp",
            logo_variants: [{ url: "https://cdn.example/saved.webp", width: 64 }],
          }
        : existing,
    ),
  );
  vi.stubGlobal("fetch", fetcher);
  render(
    <PartnerConnections
      csrfToken="csrf"
      onUnavailable={unavailable}
      route={{ view: "edit", connectionId: existing.provider }}
    />,
  );
  const name = await screen.findByRole("textbox", { name: /Connection name/ });
  await user.clear(name);
  await user.type(name, "Edited catalog");
  await user.type(
    screen.getByRole("textbox", { name: "Logo URL" }),
    "https://remote.example/logo.png",
  );
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(router.push).toHaveBeenCalledWith(connectionPath(existing.provider)));
  const writes = fetcher.mock.calls.filter(
    ([url, init]) =>
      init?.method === "PUT" || (init?.method === "POST" && String(url).endsWith("/logo")),
  );
  expect(writes).toHaveLength(1);
  expect(String(writes[0][0])).toBe(
    `/api/v1/partner/accounts/${account.id}/connections/${existing.provider}`,
  );
  expect(JSON.parse(String(writes[0][1]!.body))).toMatchObject({
    name: "Edited catalog",
    logo_url: "https://remote.example/logo.png",
    expected_revision: 1,
  });
});

it("retries a failed logo upload against the saved identity without creating another connection", async () => {
  let logoAttempts = 0;
  const saved = connection({ name: "My catalog", revision: 2 });
  const fetcher = vi.fn<typeof fetch>().mockImplementation(async (url, init) => {
    const path = String(url);
    if (path.endsWith("/logo")) {
      logoAttempts++;
      return logoAttempts === 1
        ? Response.json({ detail: "private storage failure" }, { status: 503 })
        : Response.json({
            ...saved,
            revision: 4,
            logo_url: "https://cdn.example/logo.webp",
            logo_variants: [{ url: "https://cdn.example/logo.webp", width: 64 }],
          });
    }
    if (init?.method === "POST") return Response.json(saved);
    if (init?.method === "PUT") return Response.json({ ...saved, revision: 3 });
    return Response.json(path.includes("/accounts?") ? { items: [account], total: 1 } : list());
  });
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
  const file = new File(["image"], "platform.png", { type: "image/png" });
  fireEvent.change(screen.getByLabelText("Logo image file"), { target: { files: [file] } });
  fireEvent.click(screen.getByRole("button", { name: "Create connection" }));
  expect((await screen.findByRole("alert")).textContent).toContain(
    "Connection settings saved, but the logo was not uploaded",
  );
  expect(screen.getByRole("alert").textContent).not.toContain("private storage failure");
  expect(screen.getByText("platform.png")).toBeTruthy();
  expect(router.push).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(router.push).toHaveBeenCalledWith(connectionPath(saved.provider)));
  const creates = fetcher.mock.calls.filter(
    ([url, init]) => String(url) === "/api/v1/partner/connections" && init?.method === "POST",
  );
  expect(creates).toHaveLength(1);
  expect(JSON.parse(String(creates[0][1]!.body))).not.toHaveProperty("logo_file");
  const update = fetcher.mock.calls.find(([, init]) => init?.method === "PUT")!;
  expect(JSON.parse(String(update[1]!.body)).expected_revision).toBe(2);
  const uploads = fetcher.mock.calls.filter(([url]) => String(url).endsWith("/logo"));
  expect(uploads).toHaveLength(2);
  expect((uploads[0][1]!.body as FormData).get("expected_revision")).toBe("2");
  expect((uploads[1][1]!.body as FormData).get("expected_revision")).toBe("3");
  expect((uploads[1][1]!.body as FormData).get("file")).toBe(file);
  expect(uploads[1][1]!.headers).toMatchObject({ "x-csrf-token": "csrf-value" });
});

it("blocks saving an invalid logo until it is replaced or removed", async () => {
  const save = form(connection());
  fireEvent.change(screen.getByLabelText("Logo image file"), {
    target: { files: [new File(["<svg/>"], "invalid.svg", { type: "image/svg+xml" })] },
  });
  const submit = screen.getByRole("button", { name: "Save changes" }) as HTMLButtonElement;
  expect(submit.disabled).toBe(true);
  fireEvent.submit(submit.closest("form")!);
  expect(save).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Remove logo" }));
  expect(screen.queryByRole("alert")).toBeNull();
  expect(submit.disabled).toBe(false);
  fireEvent.click(submit);
  await waitFor(() => expect(save).toHaveBeenCalledOnce());
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

it.each([true, false])(
  "keeps unchanged credentials private with credential storage available=%s",
  async (credentialsAvailable) => {
    const save = form(
      connection({
        credential_configured: true,
        auth_mode: "bearer",
        connector: {
          base_url: "https://catalog.example",
          max_pages: 1000,
          auth: { mode: "bearer" },
        },
      }),
      { credentialsAvailable },
    );
    expect(screen.queryByLabelText(/^Bearer token/)).toBeNull();
    fireEvent.change(screen.getByRole("textbox", { name: /Connection name/ }), {
      target: { value: "Renamed" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() =>
      expect(save).toHaveBeenCalledExactlyOnceWith({ name: "Renamed", enabled: true }),
    );
  },
);

it("uses explicit write-only replacement and supports removing authentication", async () => {
  const user = userEvent.setup();
  const save = form(
    connection({
      credential_configured: true,
      auth_mode: "bearer",
      connector: {
        base_url: "https://catalog.example",
        auth: { mode: "bearer" },
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
  await user.click(screen.getByRole("combobox", { name: "Authentication method" }));
  await user.click(screen.getByRole("option", { name: "No authentication" }));
  expect(screen.getByText(/Saving without authentication/).textContent).toContain(
    "removes the stored credential",
  );
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
        auth: { mode: "bearer" },
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

it("allows editing stored credentials and API settings for every owned connection", async () => {
  const save = form(
    connection({
      credential_configured: true,
      auth_mode: "bearer",
      connector: { base_url: "https://catalog.example", auth: { mode: "bearer" } },
    }),
  );
  expect(screen.getByRole("textbox", { name: /API base URL/ })).not.toBeNull();
  expect(screen.getByRole("combobox", { name: "Authentication method" })).not.toBeNull();
  expect(screen.queryByLabelText(/^Bearer token/)).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() =>
    expect(save).toHaveBeenCalledExactlyOnceWith({ name: "Product catalog", enabled: true }),
  );
});

it("truthfully disables authenticated creation without server credential storage", async () => {
  const user = userEvent.setup();
  form(undefined, { credentialsAvailable: false });
  const trigger = screen.getByRole("combobox", { name: "Authentication method" });
  await user.click(trigger);
  for (const name of ["Bearer token", "API key"]) {
    const option = screen.getByRole("option", { name });
    expect(option.getAttribute("aria-disabled")).toBe("true");
    fireEvent.click(option);
    expect(trigger.textContent).toBe("No authentication");
  }
  await user.keyboard("b{Enter}");
  expect(trigger.textContent).toBe("No authentication");
  expect(screen.queryByLabelText(/^Bearer token/)).toBeNull();
  expect(screen.queryByLabelText(/^API key$/)).toBeNull();
  expect(screen.getByText(/Authenticated imports are not configured/)).toBeTruthy();
});

it.each([
  ["none", "None", false, false],
  ["cursor", "Cursor", true, true],
  ["page", "Page number", true, false],
  ["offset", "Offset", true, false],
  ["next_url", "Next-page URL", false, true],
] as const)(
  "saves %s pagination through the shared dropdown",
  async (mode, label, parameter, next) => {
    const user = userEvent.setup();
    const save = form(connection());
    const trigger = screen.getByRole("combobox", { name: "Pagination mode" });
    expect(trigger.tagName).toBe("BUTTON");
    await user.click(trigger);
    expect(screen.getAllByRole("option")).toHaveLength(5);
    await user.click(screen.getByRole("option", { name: label }));
    await waitFor(() => expect(document.activeElement).toBe(trigger));
    expect(Boolean(screen.queryByRole("textbox", { name: "Pagination parameter" }))).toBe(
      parameter,
    );
    expect(Boolean(screen.queryByRole("textbox", { name: "Next token path" }))).toBe(next);
    expect(screen.getByRole("spinbutton", { name: "Page size" })).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() =>
      expect(save).toHaveBeenCalledWith(
        expect.objectContaining({
          connector: expect.objectContaining({ pagination: expect.objectContaining({ mode }) }),
        }),
      ),
    );
  },
);

it("uses shared checkboxes without submitting and disables all controls during saves", async () => {
  const user = userEvent.setup();
  const existing = connection({
    credential_configured: true,
    auth_mode: "bearer",
    connector: { base_url: "https://catalog.example", auth: { mode: "bearer" } },
  });
  const save = form(existing);
  const scheduled = screen.getByRole("checkbox", { name: "Enable scheduled imports" });
  expect(scheduled.tagName).toBe("BUTTON");
  await user.click(scheduled);
  expect(scheduled.getAttribute("aria-checked")).toBe("false");
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() =>
    expect(save).toHaveBeenCalledExactlyOnceWith({ name: existing.name, enabled: false }),
  );
  cleanup();
  const saving = form(existing, { saving: true });
  for (const name of ["Authentication method", "Pagination mode"]) {
    await user.click(screen.getByRole("combobox", { name }));
  }
  for (const name of ["Enable scheduled imports", "Replace credential"]) {
    await user.click(screen.getByRole("checkbox", { name }));
  }
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.queryByLabelText(/^Bearer token/)).toBeNull();
  expect(
    screen.getByRole("checkbox", { name: "Enable scheduled imports" }).getAttribute("aria-checked"),
  ).toBe("true");
  expect(saving).not.toHaveBeenCalled();
});

it("replaces unsupported existing schedules with an allowed daily interval", async () => {
  const save = form(connection({ sync_interval_minutes: 1 }));
  expect(screen.queryByRole("spinbutton", { name: /Sync interval/ })).toBeNull();
  expect(screen.getByRole("combobox", { name: "Sync interval" }).textContent).toContain("24 hours");
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() =>
    expect(save).toHaveBeenCalledWith(
      expect.objectContaining({
        sync_interval_minutes: 1440,
      }),
    ),
  );
});

it.each([
  ["bronze", [24]],
  ["silver", [24]],
  ["gold", [12, 24]],
  ["platinum", [6, 12, 24]],
  ["diamond", [1, 3, 6, 12, 24]],
] as const)("offers tier-specific sync intervals for %s", async (tier, hours) => {
  const user = userEvent.setup();
  const save = form(connection({ account_tier: tier }));
  await user.click(screen.getByRole("combobox", { name: "Sync interval" }));
  expect(screen.getAllByRole("option").map((option) => option.textContent?.trim())).toEqual(
    hours.map((hour) => `${hour} ${hour === 1 ? "hour" : "hours"}`),
  );
  await user.click(
    screen.getByRole("option", {
      name: `${hours[0]} ${hours[0] === 1 ? "hour" : "hours"}`,
    }),
  );
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(save).toHaveBeenCalled());
  if (hours[0] !== 24)
    expect(save).toHaveBeenCalledWith(
      expect.objectContaining({ sync_interval_minutes: hours[0] * 60 }),
    );
});

it("tests the edited import without saving and clears the result when configuration changes", async () => {
  const existing = connection();
  const preview = {
    discovered: 1,
    has_next_page: true,
    errors: [],
    products: [
      {
        provider: existing.provider,
        external_id: "debug-tool",
        name: "Debug console",
        tagline: "Inspect and debug Python applications with a native developer console.",
        product_url: "https://debug.example",
        listing_url: "https://catalog.example/debug-console",
        pricing: "free",
        attribution: "",
      },
    ],
  };
  const fetcher = vi
    .fn<typeof fetch>()
    .mockImplementation(async (url) =>
      Response.json(
        String(url).endsWith("connector-preview") ? preview : { data: {}, sampled: false },
      ),
    );
  vi.stubGlobal("fetch", fetcher);
  const save = form(existing);
  fireEvent.click(screen.getByRole("button", { name: "Test import connection" }));
  const result = await screen.findByRole("status", { name: "Import test result" });
  expect(result.textContent).toContain("1 product found; another page is available");
  expect(result.textContent).toContain("Debug console");
  expect(result.textContent).toContain("No products were imported");
  const [url, init] = fetcher.mock.calls.find(([url]) =>
    String(url).endsWith("connector-preview"),
  )!;
  expect(url).toBe(
    `/api/v1/partner/accounts/${account.id}/connections/${existing.provider}/connector-preview`,
  );
  expect(init?.headers).toMatchObject({ "x-csrf-token": "csrf" });
  expect(JSON.parse(String(init?.body))).toMatchObject({
    expected_revision: existing.revision,
    connector: { base_url: "https://catalog.example", max_pages: 100 },
  });
  expect(JSON.parse(String(init?.body))).not.toHaveProperty("credential");
  expect(save).not.toHaveBeenCalled();
  fireEvent.change(screen.getByRole("textbox", { name: "Product name paths" }), {
    target: { value: "title" },
  });
  expect(screen.queryByRole("status", { name: "Import test result" })).toBeNull();
});

it("sends replacement credentials only to preview and clears them when the API origin changes", async () => {
  const existing = connection({
    credential_configured: true,
    auth_mode: "bearer",
    connector: {
      base_url: "https://catalog.example",
      auth: { mode: "bearer" },
    },
  });
  const fetcher = vi
    .fn<typeof fetch>()
    .mockResolvedValue(
      Response.json({ products: [], errors: [], has_next_page: false, discovered: 0 }),
    );
  vi.stubGlobal("fetch", fetcher);
  const save = form(existing);
  fireEvent.click(screen.getByRole("checkbox", { name: "Replace credential" }));
  fireEvent.change(screen.getByLabelText(/^Bearer token/), {
    target: { value: "test-only-replacement" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Test import connection" }));
  await screen.findByRole("status", { name: "Import test result" });
  expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body)).credential).toBe(
    "test-only-replacement",
  );
  expect(save).not.toHaveBeenCalled();
  fireEvent.change(screen.getByRole("textbox", { name: "API base URL" }), {
    target: { value: "https://different.example" },
  });
  expect((screen.getByLabelText(/^Bearer token/) as HTMLInputElement).value).toBe("");
  expect(screen.queryByRole("status", { name: "Import test result" })).toBeNull();
  expect(
    (screen.getByRole("button", { name: "Test import connection" }) as HTMLButtonElement).disabled,
  ).toBe(true);
  fireEvent.change(screen.getByRole("textbox", { name: "API base URL" }), {
    target: { value: "https://catalog.example" },
  });
  expect((screen.getByLabelText(/^Bearer token/) as HTMLInputElement).value).toBe("");
  expect(
    screen.getByRole("checkbox", { name: "Replace credential" }).getAttribute("aria-checked"),
  ).toBe("true");
});

it("keeps preview rate failures actionable and does not publish a successful import test", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>().mockResolvedValue(new Response(null, { status: 429 })),
  );
  const save = form(connection());
  fireEvent.click(screen.getByRole("button", { name: "Test import connection" }));
  expect((await screen.findByRole("alert")).textContent).toContain("Wait a minute");
  expect(screen.queryByRole("status", { name: "Import test result" })).toBeNull();
  expect(save).not.toHaveBeenCalled();
});

it("saves maximum pages above previous caps without clamping", async () => {
  const save = form(connection());
  const input = screen.getByRole("spinbutton", { name: "Maximum pages" });
  expect(input.getAttribute("max")).toBeNull();
  fireEvent.change(input, { target: { value: "1000000" } });
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() =>
    expect(save).toHaveBeenCalledWith(
      expect.objectContaining({
        connector: expect.objectContaining({ max_pages: 1000000 }),
      }),
    ),
  );
});
