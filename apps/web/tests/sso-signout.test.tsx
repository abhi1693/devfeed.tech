// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { UserProvider, useUser } from "@/components/user-account";
import { ssoLogoutUrl } from "@devfeed/http/sso";

const browser = vi.hoisted(() => ({ complete: vi.fn(), cancel: vi.fn() }));
vi.mock("@/lib/reader-runtime", async (load) => ({
  ...(await load<typeof import("@/lib/reader-runtime")>()),
  prepareReaderSignOut: () => browser,
}));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

function Account() {
  const { user, loading, signOut } = useUser();
  return (
    <button
      disabled={loading}
      onClick={() => {
        void signOut().catch(() => {});
      }}
    >
      {user ? "Sign out" : "Signed out"}
    </button>
  );
}

it.each([200, 503])("coordinates local and provider logout with status %s", async (status) => {
  const fetcher = vi.fn((path: string) =>
    Promise.resolve(
      path.endsWith("auth/me")
        ? Response.json({
            user_id: "user",
            csrf_token: "csrf",
            expires_at: Date.now() / 1000 + 3600,
          })
        : path.endsWith("auth/sso/logout")
          ? Response.json({ url: "https://issuer.test/end-session?state=bound" }, { status })
          : Response.json({}),
    ),
  );
  vi.stubGlobal("fetch", fetcher);
  render(
    <UserProvider>
      <Account />
    </UserProvider>,
  );
  const button = await screen.findByRole("button", { name: "Sign out" });
  await waitFor(() => expect(button.hasAttribute("disabled")).toBe(false));
  fireEvent.click(button);
  if (status === 200) {
    await waitFor(() =>
      expect(browser.complete).toHaveBeenCalledWith("https://issuer.test/end-session?state=bound"),
    );
    expect(browser.cancel).not.toHaveBeenCalled();
  } else {
    await waitFor(() => expect(browser.cancel).toHaveBeenCalledOnce());
    expect(browser.complete).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeTruthy();
  }
  const call = fetcher.mock.calls.find(([path]) => path.endsWith("auth/sso/logout"));
  expect(call).toBeTruthy();
  expect(fetcher.mock.calls.some(([path]) => path.endsWith("auth/logout"))).toBe(false);
});

it.each([
  {},
  { url: "/login" },
  { url: "javascript:alert(1)" },
  { url: "http://issuer.test/logout" },
  { url: "https://user:password@issuer.test/logout" },
])("rejects unsafe sign-out destinations %j", (value) => {
  expect(() => ssoLogoutUrl(value)).toThrow();
});
