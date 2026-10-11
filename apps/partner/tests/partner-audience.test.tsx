// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { PartnerAudience } from "../src/app/partner-audience";
import type { Account } from "../src/lib/types";

const router = vi.hoisted(() => ({ replace: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => router }));
const audience = {
  experience_levels: ["senior" as const],
  language_topic_ids: [],
  tool_topic_ids: [],
};
const account: Account = {
  id: "11111111-1111-4111-8111-111111111111",
  name: "Example",
  tier: "platinum",
  benefits: ["Advanced audience targeting"],
  status: "active",
  advanced_targeting: true,
  audience,
  outbound_destination: "listing_url",
};
const saved = vi.fn();
const unavailable = vi.fn();
function mount(value = account) {
  return render(
    <PartnerAudience
      key={value.id}
      account={value}
      csrfToken="partner-csrf"
      onSaved={saved}
      onUnavailable={unavailable}
    />,
  );
}
beforeEach(() => vi.clearAllMocks());
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("saves only audience preferences with CSRF while preserving the click destination", async () => {
  const fetcher = vi.fn(async () =>
    Response.json({
      ...account,
      audience: { ...audience, experience_levels: ["senior", "entry_level"] },
    }),
  );
  vi.stubGlobal("fetch", fetcher);
  mount();
  expect(fetcher).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("checkbox", { name: "Entry level" }));
  fireEvent.click(screen.getByRole("button", { name: "Save audience" }));
  await screen.findByText("Audience saved.");
  expect(fetcher).toHaveBeenCalledExactlyOnceWith(
    `/api/v1/partner/accounts/${account.id}/preferences`,
    expect.objectContaining({
      method: "PUT",
      headers: { "content-type": "application/json", "x-csrf-token": "partner-csrf" },
      body: JSON.stringify({
        audience: { ...audience, experience_levels: ["senior", "entry_level"] },
      }),
      signal: expect.any(AbortSignal),
    }),
  );
  expect(saved).toHaveBeenCalledWith(
    expect.objectContaining({ outbound_destination: "listing_url" }),
  );
});

it("shows retained lower-tier restrictions without saveable controls", () => {
  vi.stubGlobal("fetch", vi.fn());
  mount({ ...account, tier: "gold", advanced_targeting: false });
  expect(screen.getByText(/Advanced targeting requires Platinum or Diamond/)).toBeTruthy();
  expect(screen.getByText(/Saved restrictions are retained/)).toBeTruthy();
  expect(screen.getByText("Senior")).toBeTruthy();
  expect(screen.queryByRole("checkbox")).toBeNull();
  expect(screen.queryByRole("combobox")).toBeNull();
  expect(screen.queryByRole("button", { name: "Save audience" })).toBeNull();
});

it("loads scoped server-filtered topics and resolves saved names on edit", async () => {
  const rust = { id: "rust", name: "Rust", slug: "rust", kind: "language", status: "active" };
  const fetcher = vi.fn(async () => Response.json({ items: [rust], total: 1 }));
  vi.stubGlobal("fetch", fetcher);
  mount({ ...account, audience: { ...audience, language_topic_ids: [rust.id] } });
  await screen.findByText("Rust");
  expect(fetcher).toHaveBeenCalledWith(
    `/api/v1/partner/accounts/${account.id}/audience-topics?dimension=languages&topic_id=rust&limit=1`,
    expect.objectContaining({ cache: "no-store", signal: expect.any(AbortSignal) }),
  );
  fireEvent.click(screen.getByRole("combobox", { name: "Developer tools" }));
  await waitFor(() =>
    expect(fetcher).toHaveBeenCalledWith(
      `/api/v1/partner/accounts/${account.id}/audience-topics?dimension=tools&q=&offset=0&limit=25`,
      expect.any(Object),
    ),
  );
});

it.each([401, 404])("clears access when saving returns %s", async (status) => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(null, { status })),
  );
  mount();
  fireEvent.click(screen.getByRole("button", { name: "Save audience" }));
  await waitFor(() => expect(unavailable).toHaveBeenCalledOnce());
  expect(saved).not.toHaveBeenCalled();
  if (status === 401) expect(router.replace).toHaveBeenCalledWith("/login");
});

it("keeps selections after a rejected save and supports retry", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(new Response(null, { status: 403 }))
    .mockImplementation(async () => Response.json(account));
  vi.stubGlobal("fetch", fetcher);
  mount();
  fireEvent.click(screen.getByRole("button", { name: "Save audience" }));
  await screen.findByText(/Could not update this account/);
  expect((screen.getByRole("checkbox", { name: "Senior" }) as HTMLInputElement).checked).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Save audience" }));
  await screen.findByText("Audience saved.");
  expect(unavailable).not.toHaveBeenCalled();
});

it.each([
  { ...account, id: "another-account" },
  { ...account, audience: { experience_levels: ["unknown"] } },
])("rejects an invalid saved audience response", async (response) => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json(response)),
  );
  mount();
  fireEvent.click(screen.getByRole("button", { name: "Save audience" }));
  await screen.findByText(/Could not confirm the saved audience/);
  expect(saved).not.toHaveBeenCalled();
});

it("aborts a pending save on unmount and ignores a late result", async () => {
  let resolve!: (response: Response) => void;
  const fetcher = vi.fn(
    () =>
      new Promise<Response>((done) => {
        resolve = done;
      }),
  );
  vi.stubGlobal("fetch", fetcher);
  const view = mount();
  fireEvent.click(screen.getByRole("button", { name: "Save audience" }));
  const signal = (fetcher.mock.calls[0] as unknown as [string, RequestInit])[1].signal!;
  view.unmount();
  expect(signal.aborted).toBe(true);
  resolve(Response.json(account));
  await Promise.resolve();
  expect(saved).not.toHaveBeenCalled();
});
