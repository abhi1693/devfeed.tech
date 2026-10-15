// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { PartnerConnections } from "../src/app/partner-connections";
import type {
  PartnerConnectionJobOut,
  PartnerConnectionJobsOut,
  WorkspaceConnectionOut,
} from "../src/lib/api/generated/models";
import { partnerAccount } from "./partner-fixtures";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

const account = partnerAccount();
const unavailable = vi.fn();

function connection(overrides: Partial<WorkspaceConnectionOut> = {}): WorkspaceConnectionOut {
  return {
    provider: "catalog",
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
    ...overrides,
  };
}

function jobs(status?: string): PartnerConnectionJobsOut {
  const job: PartnerConnectionJobOut = {
    id: "22222222-2222-4222-8222-222222222222",
    operation: "sync",
    status: status ?? "succeeded",
    attempts: 1,
    created_at: "2026-10-15T12:00:00Z",
    finished_at: status === "succeeded" ? "2026-10-15T12:01:00Z" : null,
    error: null,
  };
  return { items: status ? [job] : [], total: status ? 1 : 0, limit: 10, offset: 0 };
}

function isJobs(input: Parameters<typeof fetch>[0]) {
  return String(input).includes("/jobs?");
}

function total() {
  return screen.getByText("Total", { selector: "dt" }).nextElementSibling?.textContent;
}

function run() {
  return screen.getByRole("row", { name: /Catalog sync/ });
}

async function renderDetail(connectionId = "catalog") {
  let view!: ReturnType<typeof render>;
  await act(async () => {
    view = render(
      <PartnerConnections
        csrfToken="csrf"
        onUnavailable={unavailable}
        route={{ view: "detail", connectionId }}
      />,
    );
  });
  return view;
}

async function advance(milliseconds = 3000) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(milliseconds);
  });
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((finish) => {
    resolve = finish;
  });
  return { promise, resolve };
}

beforeEach(() => {
  vi.useFakeTimers();
  unavailable.mockClear();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

it("updates queued import runs and product totals through completion without clearing the detail", async () => {
  let phase = 0;
  const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input) => {
    if (isJobs(input)) return Response.json(jobs(["queued", "running", "succeeded"][phase]));
    return Response.json(
      connection({
        state: phase === 1 ? "syncing" : "idle",
        products: [12, 20, 30][phase],
      }),
    );
  });
  vi.stubGlobal("fetch", fetcher);
  await renderDetail();
  expect(within(run()).getByText("queued")).toBeTruthy();
  expect(total()).toBe("12");

  phase = 1;
  await advance();
  expect(within(run()).getByText("running")).toBeTruthy();
  expect(within(run()).getByText("In progress")).toBeTruthy();
  expect(screen.getByText("Syncing")).toBeTruthy();
  expect(total()).toBe("20");
  expect(screen.queryByText(/Loading import/)).toBeNull();

  phase = 2;
  await advance();
  expect(within(run()).getByText("succeeded")).toBeTruthy();
  expect(screen.getByText("Ready")).toBeTruthy();
  expect(total()).toBe("30");
  const requestsAfterCompletion = fetcher.mock.calls.length;
  await advance(60000);
  expect(fetcher).toHaveBeenCalledTimes(requestsAfterCompletion);
});

it("refreshes a syncing connection even before its run appears in the recent page", async () => {
  let finished = false;
  const fetcher = vi
    .fn<typeof fetch>()
    .mockImplementation(async (input) =>
      Response.json(
        isJobs(input)
          ? jobs(finished ? "succeeded" : undefined)
          : connection({ state: finished ? "idle" : "syncing", products: finished ? 25 : 12 }),
      ),
    );
  vi.stubGlobal("fetch", fetcher);
  await renderDetail();
  expect(screen.getByText("Syncing")).toBeTruthy();
  finished = true;
  await advance();
  expect(within(run()).getByText("succeeded")).toBeTruthy();
  expect(screen.getByText("Ready")).toBeTruthy();
  expect(total()).toBe("25");
});

it("waits for a slow status refresh and pauses automatic work while the page is hidden", async () => {
  const pending = deferred<Response>();
  let detailReads = 0;
  const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input) => {
    if (isJobs(input)) return Response.json(jobs("running"));
    detailReads++;
    return detailReads === 2 ? pending.promise : Response.json(connection({ state: "syncing" }));
  });
  vi.stubGlobal("fetch", fetcher);
  await renderDetail();
  await advance(30000);
  expect(detailReads).toBe(2);
  expect(within(run()).getByText("running")).toBeTruthy();

  await act(async () => pending.resolve(Response.json(connection({ state: "syncing" }))));
  const visible = vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
  await act(async () => document.dispatchEvent(new Event("visibilitychange")));
  const hiddenRequests = fetcher.mock.calls.length;
  await advance(60000);
  expect(fetcher).toHaveBeenCalledTimes(hiddenRequests);

  visible.mockReturnValue("visible");
  await act(async () => document.dispatchEvent(new Event("visibilitychange")));
  expect(detailReads).toBe(3);
});

it("aborts a status refresh on navigation and ignores a late response for the old connection", async () => {
  const pending = deferred<Response>();
  let oldReads = 0;
  let staleSignal: AbortSignal | null | undefined;
  const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
    if (String(input).includes("/other-catalog"))
      return Response.json(
        isJobs(input) ? jobs() : connection({ provider: "other-catalog", name: "Other catalog" }),
      );
    if (isJobs(input)) return Response.json(jobs("queued"));
    oldReads++;
    if (oldReads === 2) {
      staleSignal = init?.signal;
      return pending.promise;
    }
    return Response.json(connection());
  });
  vi.stubGlobal("fetch", fetcher);
  const view = await renderDetail();
  await advance();
  expect(staleSignal?.aborted).toBe(false);
  await act(async () => {
    view.rerender(
      <PartnerConnections
        csrfToken="csrf"
        onUnavailable={unavailable}
        route={{ view: "detail", connectionId: "other-catalog" }}
      />,
    );
  });
  expect(staleSignal?.aborted).toBe(true);
  expect(screen.getByRole("heading", { name: "Other catalog" })).toBeTruthy();
  await act(async () => {
    pending.resolve(Response.json(connection({ name: "Stale catalog", products: 999 })));
  });
  expect(screen.queryByText("Stale catalog")).toBeNull();
  expect(total()).toBe("12");
  const requestsAfterNavigation = fetcher.mock.calls.length;
  await advance(60000);
  expect(fetcher).toHaveBeenCalledTimes(requestsAfterNavigation);
  expect(unavailable).not.toHaveBeenCalled();
});

it.each(["connection", "jobs"])(
  "preserves known status during a transient %s failure and recovers automatically",
  async (failingResource) => {
    let phase = 0;
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input) => {
      const resource = isJobs(input) ? "jobs" : "connection";
      if (phase === 1 && resource === failingResource)
        return Response.json({ detail: "private upstream error" }, { status: 503 });
      return Response.json(
        isJobs(input)
          ? jobs(phase === 2 ? "succeeded" : "running")
          : connection({
              state: phase === 2 ? "idle" : "syncing",
              products: phase === 2 ? 24 : 12,
            }),
      );
    });
    vi.stubGlobal("fetch", fetcher);
    await renderDetail();
    phase = 1;
    await advance();
    expect(within(run()).getByText("running")).toBeTruthy();
    expect(total()).toBe("12");
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toBe("Could not refresh import status. Refresh to try again.");
    expect(alert.textContent).not.toContain("private upstream error");
    expect(screen.getByRole("button", { name: "Refresh" })).toBeTruthy();

    phase = 2;
    await advance();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(within(run()).getByText("succeeded")).toBeTruthy();
    expect(total()).toBe("24");
    expect(unavailable).not.toHaveBeenCalled();
  },
);

it.each(["detail", "edit"] as const)(
  "keeps automatic polling off for a %s page without active detail work",
  async (view) => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockImplementation(async (input) =>
        Response.json(
          String(input).endsWith("/connector-response")
            ? { data: {}, sampled: false }
            : isJobs(input)
              ? jobs("succeeded")
              : connection({ state: view === "edit" ? "syncing" : "idle" }),
        ),
      );
    vi.stubGlobal("fetch", fetcher);
    await act(async () => {
      render(
        <PartnerConnections
          csrfToken="csrf"
          onUnavailable={unavailable}
          route={{ view, connectionId: "catalog" }}
        />,
      );
    });
    if (view === "edit") {
      fireEvent.change(screen.getByRole("textbox", { name: /Connection name/ }), {
        target: { value: "Unsaved catalog name" },
      });
    }
    const statusReads = () =>
      fetcher.mock.calls.filter(
        ([input, init]) =>
          (init?.method ?? "GET") === "GET" &&
          (String(input).endsWith("/connections/catalog") || isJobs(input)),
      ).length;
    const initialRequests = statusReads();
    await advance(60000);
    expect(statusReads()).toBe(initialRequests);
    if (view === "edit")
      expect(
        (screen.getByRole("textbox", { name: /Connection name/ }) as HTMLInputElement).value,
      ).toBe("Unsaved catalog name");
  },
);
