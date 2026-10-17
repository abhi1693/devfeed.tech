// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { TagFollow } from "@/components/tag-follow";
import { TagFollowsProvider } from "@/components/tag-follows";
import { UserProvider } from "@/components/user-account";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("offers optional sign-in returning to the article without writing preferences", async () => {
  const fetcher = vi.fn().mockResolvedValue(Response.json(null));
  vi.stubGlobal("fetch", fetcher);
  render(
    <UserProvider>
      <TagFollowsProvider>
        <TagFollow tagId="tag-a" returnTo="/tags/c%2B%2B" />
      </TagFollowsProvider>
    </UserProvider>,
  );
  const link = await screen.findByRole("link", { name: "Follow" });
  expect(link.getAttribute("href")).toBe("/login?return_to=%2Ftags%2Fc%252B%252B");
  expect(fetcher).toHaveBeenCalledTimes(1);
});

it("changes only this tag and retains the saved state when a write fails", async () => {
  let fail = false;
  const fetcher = vi.fn((url: string, init?: RequestInit) =>
    Promise.resolve(
      url.endsWith("auth/me")
        ? Response.json({ user_id: "user-a", csrf_token: "csrf" })
        : init?.method === "PUT"
          ? fail
            ? Response.json({}, { status: 503 })
            : Response.json({ followed: true })
          : Response.json({ tag_ids: ["another-tag"] }),
    ),
  );
  vi.stubGlobal("fetch", fetcher);
  render(
    <UserProvider>
      <TagFollowsProvider>
        <TagFollow tagId="tag-a" returnTo="/tags/c%2B%2B" />
      </TagFollowsProvider>
    </UserProvider>,
  );
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Follow" })).toHaveProperty("disabled", false),
  );
  fireEvent.click(screen.getByRole("button", { name: "Follow" }));
  await screen.findByRole("button", { name: "Following" });
  const [url, options] = fetcher.mock.calls.find(([, init]) => init?.method === "PUT")!;
  expect(url).toBe("/api/v1/user/preferences/tags/tag-a");
  expect(options?.body).toBe('{"followed":true}');
  expect(options?.headers).toEqual({
    "Content-Type": "application/json",
    "X-CSRF-Token": "csrf",
  });
  fail = true;
  fireEvent.click(screen.getByRole("button", { name: "Following" }));
  await screen.findByRole("alert");
  expect(screen.getByRole("button", { name: "Following" }).getAttribute("aria-pressed")).toBe(
    "true",
  );
});

it("retries loading tags when the tab resumes after a failed request", async () => {
  let preferenceReads = 0;
  const fetcher = vi.fn((url: string) =>
    Promise.resolve(
      url.endsWith("auth/me")
        ? Response.json({ user_id: "user-a", csrf_token: "csrf" })
        : ++preferenceReads === 1
          ? Response.json({}, { status: 503 })
          : Response.json({ tag_ids: ["tag-a"] }),
    ),
  );
  vi.stubGlobal("fetch", fetcher);
  render(
    <UserProvider>
      <TagFollowsProvider>
        <TagFollow tagId="tag-a" />
      </TagFollowsProvider>
    </UserProvider>,
  );
  await screen.findByRole("alert");
  expect(screen.getByRole("button", { name: "Follow" })).toHaveProperty("disabled", true);

  window.dispatchEvent(new Event("focus"));

  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Following" })).toHaveProperty("disabled", false),
  );
  expect(preferenceReads).toBeGreaterThanOrEqual(2);
});
