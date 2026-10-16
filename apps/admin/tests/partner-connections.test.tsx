// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { renderAdmin } from "./render-admin";
import { PartnerConnections } from "@/components/organisms/partner-connections";
import { adminPartnerConnectionsList } from "@/lib/api/generated/admin";
import { ApiError } from "@/lib/api/client";
import type { ConnectionOut } from "@/lib/api/generated/models";

const navigation = vi.hoisted(() => ({ query: "", replace: vi.fn() }));
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(navigation.query),
  useRouter: () => ({ replace: navigation.replace }),
}));
vi.mock("@/lib/use-refresh-interval", () => ({ useRefreshInterval: () => 0 }));
vi.mock("@/lib/api/generated/admin", () => ({ adminPartnerConnectionsList: vi.fn() }));
const connection: ConnectionOut = {
  provider: "late-platform",
  name: "Later platform",
  partnership_type: "launch_platform",
  account_id: null,
  api_url: "https://later.example/api",
  revision: 1,
  sync_interval_minutes: 360,
  enabled: true,
  state: "idle",
  last_sync_at: null,
  next_sync_at: null,
  error: null,
  products: 8,
  qualified: 5,
  checking: 2,
  needs_attention: 1,
  excluded: 0,
  ai_enabled: true,
};
const page = { items: [connection], total: 51, offset: 25, limit: 25 };
beforeEach(() => {
  vi.clearAllMocks();
  navigation.query = "offset=25&limit=25&q=platform&sort=-name";
  vi.mocked(adminPartnerConnectionsList).mockReset().mockResolvedValue(page);
});
afterEach(cleanup);

function nextQuery() {
  const href = navigation.replace.mock.calls.at(-1)?.[0];
  expect(href).toMatch(/^\/partnerships\/connections\?/);
  return new URL(href, "https://admin.example").searchParams;
}

it("loads only the requested page and keeps search and sorting when paging", async () => {
  renderAdmin(<PartnerConnections />);
  expect(await screen.findByRole("link", { name: connection.name })).toBeTruthy();
  expect(adminPartnerConnectionsList).toHaveBeenCalledWith(
    { q: "platform", sort: "-name", offset: 25, limit: 25 },
    { signal: expect.any(AbortSignal) },
  );
  expect(screen.getByText("26–26 of 51")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Next" }));
  expect(Object.fromEntries(nextQuery())).toEqual({
    offset: "50",
    limit: "25",
    q: "platform",
    sort: "-name",
  });
  expect(navigation.replace).toHaveBeenLastCalledWith(expect.any(String), { scroll: false });
});

it("starts a new search at the first page without changing the page size", async () => {
  renderAdmin(<PartnerConnections />);
  await screen.findByRole("link", { name: connection.name });
  fireEvent.change(screen.getByRole("textbox", { name: "Search import connections" }), {
    target: { value: "new platform" },
  });
  fireEvent.submit(screen.getByRole("search", { name: "Search import connections" }));
  expect(Object.fromEntries(nextQuery())).toEqual({
    offset: "0",
    limit: "25",
    q: "new platform",
    sort: "-name",
  });
});

it("restarts sorting at the first page and exposes only supported sortable columns", async () => {
  renderAdmin(<PartnerConnections />);
  await screen.findByRole("link", { name: connection.name });
  fireEvent.click(screen.getByRole("button", { name: /^Name/ }));
  expect(Object.fromEntries(nextQuery())).toEqual({
    offset: "0",
    limit: "25",
    q: "platform",
    sort: "name",
  });
  expect(screen.queryByRole("button", { name: /^Products$/ })).toBeNull();
});

it("bounds URL pagination and ignores an unsupported sort before requesting", async () => {
  navigation.query = "offset=-1&limit=1000&sort=unknown";
  renderAdmin(<PartnerConnections />);
  await screen.findByRole("link", { name: connection.name });
  expect(adminPartnerConnectionsList).toHaveBeenCalledWith(
    { q: "", sort: "provider", offset: 0, limit: 100 },
    { signal: expect.any(AbortSignal) },
  );
});

it("retries a failed page without losing search or pagination", async () => {
  vi.mocked(adminPartnerConnectionsList).mockRejectedValueOnce(new ApiError(503));
  renderAdmin(<PartnerConnections />);
  await screen.findByRole("alert");
  fireEvent.click(screen.getByRole("button", { name: /retry/i }));
  await screen.findByRole("link", { name: connection.name });
  await waitFor(() => expect(adminPartnerConnectionsList).toHaveBeenCalledTimes(2));
  for (const [params] of vi.mocked(adminPartnerConnectionsList).mock.calls)
    expect(params).toEqual({ q: "platform", sort: "-name", offset: 25, limit: 25 });
  expect(
    (screen.getByRole("textbox", { name: "Search import connections" }) as HTMLInputElement).value,
  ).toBe("platform");
});
