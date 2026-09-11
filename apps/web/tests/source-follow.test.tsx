// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { UserProvider } from "@/components/user-account";
import { SourceFollow, SourceFollowsProvider } from "@/components/source-follow";
import { SourcePreferences } from "@/components/source-preferences";
import { source } from "./fixtures";
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
function setup(signedIn = true) {
  let ids: string[] = []; let fail = false;
  const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith("auth/me")) return Response.json(signedIn ? { user_id: "one", csrf_token: "csrf" } : null);
    if (url.endsWith("settings/profile")) return Response.json({});
    if (init?.method === "PUT") {
      if (fail) return Response.json({}, { status: 503 });
      const payload = JSON.parse(String(init.body));
      if ("source_ids" in payload) { ids = payload.source_ids; return Response.json({ source_ids: ids }); }
      ids = payload.followed ? [source.id] : [];
      return Response.json({ followed: payload.followed });
    }
    return Response.json({ source_ids: ids });
  });
  vi.stubGlobal("fetch", fetcher);
  return { fetcher, fail: () => { fail = true; }, ids: () => ids };
}
const shell = (children: React.ReactNode) => <UserProvider><SourceFollowsProvider>{children}</SourceFollowsProvider></UserProvider>;
it("shares one preference read across source controls and synchronizes follow state", async () => {
  const mock = setup();
  render(shell(<><SourceFollow sourceId={source.id} returnTo={`/sources/${source.id}`} /><SourceFollow sourceId={source.id} returnTo="/articles/test" /></>));
  await waitFor(() => expect((screen.getAllByRole("button", { name: "Follow source" })[0] as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getAllByRole("button", { name: "Follow source" })[0]);
  await waitFor(() => expect(screen.getAllByRole("button", { name: "Following source" })).toHaveLength(2));
  expect(mock.fetcher.mock.calls.filter(([url, init]) => url.endsWith("preferences/sources") && !init?.method)).toHaveLength(1);
  const put = mock.fetcher.mock.calls.find(([, init]) => init?.method === "PUT")![1]!;
  expect(put.headers).toEqual({ "Content-Type": "application/json", "X-CSRF-Token": "csrf" });
  mock.fail();fireEvent.click(screen.getAllByRole("button", { name: "Following source" })[0]);
  await screen.findAllByRole("alert");expect(mock.ids()).toEqual([source.id]);
});
it("keeps browsing anonymous and sends sign-in back to the source", async () => {
  const mock = setup(false);render(shell(<SourceFollow sourceId={source.id} returnTo={`/sources/${source.id}`} />));
  const link = await screen.findByRole("link", { name: "Follow source" });
  expect(link.getAttribute("href")).toContain(encodeURIComponent(`/sources/${source.id}`));
  expect(mock.fetcher.mock.calls.some(([url]) => url.includes("preferences/sources"))).toBe(false);
});
it("saves source selections from settings with the existing tile pattern", async () => {
  const mock = setup();render(shell(<SourcePreferences sources={[source]} />));
  const tile = await screen.findByRole("button", { name: source.name });
  fireEvent.click(tile);expect(tile.getAttribute("aria-pressed")).toBe("true");
  expect(screen.queryByRole("checkbox")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Save sources" }));
  await screen.findByText("Your sources are saved.");expect(mock.ids()).toEqual([source.id]);
});
