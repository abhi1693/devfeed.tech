// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ReaderFeedLayout } from "@/components/reader-feed-layout";

const preferences = vi.hoisted(() => ({
  view: "columns",
  setView: vi.fn(),
  setColumns: vi.fn(),
  save: vi.fn(),
}));
const session = vi.hoisted(() => ({ user: null as { user_id: string } | null }));
const workspaceImport = vi.hoisted(() => vi.fn());
vi.mock("@/components/feed-preferences", () => ({ useFeedPreferences: () => preferences }));
vi.mock("@/components/user-account", () => ({ useUser: () => session }));
vi.mock("@/components/columns-workspace", () => {
  workspaceImport();
  return {
    ColumnsWorkspace: ({ workspace }: { workspace?: string }) => (
      <section aria-label="Columns workspace" data-workspace={workspace}>
        <input aria-label="Workspace draft" defaultValue="saved" />
      </section>
    ),
  };
});

beforeEach(() => {
  preferences.view = "columns";
  session.user = { user_id: "reader-a" };
  vi.clearAllMocks();
  window.history.replaceState(null, "", "/");
});
afterEach(() => {
  cleanup();
  window.history.replaceState(null, "", "/");
});
function expectNoPreferenceWrites() {
  expect(preferences.setView).not.toHaveBeenCalled();
  expect(preferences.setColumns).not.toHaveBeenCalled();
  expect(preferences.save).not.toHaveBeenCalled();
}

it("defers Columns until eligible and discards its pending private render on sign-out", async () => {
  const content = (workspace: "latest" | "my_feed" = "my_feed") => (
    <ReaderFeedLayout allowColumns workspace={workspace}>
      <p>Route articles</p>
    </ReaderFeedLayout>
  );
  session.user = null;
  const app = render(content());
  await act(async () => {});
  expect(workspaceImport).not.toHaveBeenCalled();
  session.user = { user_id: "reader-a" };
  for (const view of ["cards", "compact", "stacked"]) {
    preferences.view = view;
    app.rerender(content());
    await act(async () => {});
    expect(workspaceImport).not.toHaveBeenCalled();
    expect(screen.getByText("Route articles")).toBeTruthy();
  }
  preferences.view = "columns";
  app.rerender(content("latest"));
  await act(async () => {});
  expect(workspaceImport).not.toHaveBeenCalled();
  app.rerender(content());
  expect(screen.getByRole("status", { name: "Loading columns…" })).toBeTruthy();
  session.user = null;
  app.rerender(content());
  await waitFor(() => expect(workspaceImport).toHaveBeenCalledOnce());
  expect(screen.queryByRole("region", { name: "Columns workspace" })).toBeNull();
  expect(screen.getByText("Route articles")).toBeTruthy();
  session.user = { user_id: "reader-b" };
  app.rerender(content());
  expect(await screen.findByRole("region", { name: "Columns workspace" })).toBeTruthy();
  expect(screen.queryByRole("status", { name: "Loading columns…" })).toBeNull();
  expectNoPreferenceWrites();
});

it("keeps a route's own articles by default when Columns is saved globally", () => {
  render(
    <ReaderFeedLayout>
      <p>Topic articles</p>
    </ReaderFeedLayout>,
  );
  expect(screen.getByText("Topic articles")).toBeTruthy();
  expect(screen.queryByRole("region", { name: "Columns workspace" })).toBeNull();
  expectNoPreferenceWrites();
});

it.each([
  ["guest", "latest", false],
  ["guest", "my_feed", false],
  ["account", "latest", false],
  ["account", "my_feed", true],
] as const)(
  "allows Columns for %s in %s only when it is authenticated My feed (%s)",
  async (owner, workspace, eligible) => {
    session.user = owner === "account" ? { user_id: "reader-a" } : null;
    render(
      <ReaderFeedLayout allowColumns workspace={workspace}>
        <p>Route articles</p>
      </ReaderFeedLayout>,
    );
    if (eligible) {
      expect(
        (await screen.findByRole("region", { name: "Columns workspace" })).getAttribute(
          "data-workspace",
        ),
      ).toBe("my_feed");
      expect(screen.queryByText("Route articles")).toBeNull();
    } else {
      expect(screen.queryByRole("region", { name: "Columns workspace" })).toBeNull();
      expect(screen.getByText("Route articles")).toBeTruthy();
    }
    expectNoPreferenceWrites();
  },
);

it.each(["cards", "compact", "stacked"])("keeps the %s feed on an eligible route", (view) => {
  preferences.view = view;
  render(
    <ReaderFeedLayout allowColumns workspace="my_feed">
      <p>My feed articles</p>
    </ReaderFeedLayout>,
  );
  expect(screen.getByText("My feed articles")).toBeTruthy();
  expect(screen.queryByRole("region", { name: "Columns workspace" })).toBeNull();
});

it("switches between route content and the saved workspace without rewriting preferences", async () => {
  const app = render(
    <ReaderFeedLayout allowColumns workspace="my_feed">
      <p>My feed articles</p>
    </ReaderFeedLayout>,
  );
  expect(await screen.findByRole("region", { name: "Columns workspace" })).toBeTruthy();
  app.rerender(
    <ReaderFeedLayout allowColumns workspace="latest">
      <p>Latest articles</p>
    </ReaderFeedLayout>,
  );
  expect(screen.getByText("Latest articles")).toBeTruthy();
  expect(screen.queryByRole("region", { name: "Columns workspace" })).toBeNull();
  app.rerender(
    <ReaderFeedLayout allowColumns workspace="my_feed">
      <p>My feed articles</p>
    </ReaderFeedLayout>,
  );
  expect(await screen.findByRole("region", { name: "Columns workspace" })).toBeTruthy();
  expect(preferences.view).toBe("columns");
  expectNoPreferenceWrites();
});

it("retains an eligible workspace while an article preview changes the URL", async () => {
  const app = render(
    <ReaderFeedLayout allowColumns workspace="my_feed">
      <p>My feed articles</p>
    </ReaderFeedLayout>,
  );
  const draft = await screen.findByRole("textbox", { name: "Workspace draft" });
  fireEvent.change(draft, { target: { value: "retained column state" } });
  window.history.pushState(null, "", "/articles/previewed-article");
  app.rerender(
    <ReaderFeedLayout allowColumns workspace="my_feed">
      <p>Background articles</p>
    </ReaderFeedLayout>,
  );
  expect(screen.getByRole("textbox", { name: "Workspace draft" })).toBe(draft);
  expect(draft).toHaveProperty("value", "retained column state");
  expectNoPreferenceWrites();
});

it("removes the private workspace when the account signs out without rewriting its saved layout", async () => {
  const content = () => (
    <ReaderFeedLayout allowColumns workspace="my_feed">
      <p>Public articles</p>
    </ReaderFeedLayout>
  );
  const app = render(content());
  expect(await screen.findByRole("region", { name: "Columns workspace" })).toBeTruthy();
  session.user = null;
  app.rerender(content());
  expect(screen.queryByRole("region", { name: "Columns workspace" })).toBeNull();
  expect(screen.getByText("Public articles")).toBeTruthy();
  expect(preferences.view).toBe("columns");
  expectNoPreferenceWrites();
});
