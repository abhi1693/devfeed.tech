// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ArticleEngagement, ArticleReadLink } from "@/components/article-engagement";
import { userRequest, type UserIdentity } from "@/lib/user";

const account = vi.hoisted(() => ({ user: null as UserIdentity | null, loading: false }));
vi.mock("@/components/user-account", () => ({ useUser: () => account }));
vi.mock("@/lib/user", () => ({ userRequest: vi.fn() }));
beforeEach(() => {
  account.user = null;
  account.loading = false;
  vi.mocked(userRequest)
    .mockReset()
    .mockResolvedValue({ article_id: "article", opens: 1, likes: 0, liked: false });
});
afterEach(cleanup);

it("records anonymous clicks while the optional session probe is still loading", async () => {
  account.loading = true;
  preview();
  fireEvent.click(screen.getByRole("link", { name: "Read article" }));
  await waitFor(() =>
    expect(userRequest).toHaveBeenCalledWith("articles/article/open", {
      method: "POST",
      keepalive: true,
      headers: {},
    }),
  );
});

function preview() {
  return render(
    <>
      <ArticleEngagement articleId="article" articleSlug="article-slug" />
      <ArticleReadLink
        articleId="article"
        href="https://publisher.example/article?utm_source=devfeed"
        target="_blank"
        rel="noopener noreferrer"
      >
        Read article
      </ArticleReadLink>
    </>,
  );
}

it("records no view on preview mount and records an anonymous outbound click", async () => {
  preview();
  expect(userRequest).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("link", { name: "Read article" }));
  await waitFor(() =>
    expect(userRequest).toHaveBeenCalledWith("articles/article/open", {
      method: "POST",
      keepalive: true,
      headers: {},
    }),
  );
});

it("includes signed-in CSRF and supports opening the original with the middle button", async () => {
  account.user = {
    user_id: "user",
    csrf_token: "csrf",
    name: null,
    email: null,
    expires_at: 4102444800,
  };
  preview();
  const link = screen.getByRole("link", { name: "Read article" });
  fireEvent(link, new MouseEvent("auxclick", { bubbles: true, button: 2 }));
  expect(userRequest).not.toHaveBeenCalled();
  fireEvent(link, new MouseEvent("auxclick", { bubbles: true, button: 1 }));
  await waitFor(() =>
    expect(userRequest).toHaveBeenCalledWith("articles/article/open", {
      method: "POST",
      keepalive: true,
      headers: { "X-CSRF-Token": "csrf" },
    }),
  );
});

it("keeps the original link usable when tracking fails or is throttled", async () => {
  vi.mocked(userRequest).mockRejectedValue(new Error("Throttled"));
  preview();
  const link = screen.getByRole("link", { name: "Read article" });
  expect(fireEvent.click(link)).toBe(true);
  expect(link.getAttribute("href")).toBe("https://publisher.example/article?utm_source=devfeed");
  await waitFor(() => expect(userRequest).toHaveBeenCalledOnce());
});

it("emits the GA action only on an original-article click, even if backend counting fails", async () => {
  const { analyticsEventName } = await import("@/lib/analytics");
  const spy = vi.spyOn(window, "dispatchEvent");
  vi.mocked(userRequest).mockRejectedValue(new Error("Throttled"));
  preview();
  const gaEvents = () =>
    spy.mock.calls
      .map(([event]) => event)
      .filter((event) => event.type === analyticsEventName) as CustomEvent[];
  expect(gaEvents()).toHaveLength(0);
  fireEvent.click(screen.getByRole("link", { name: "Read article" }));
  expect(gaEvents().map((event) => event.detail)).toEqual([
    { name: "article_open", params: { article_id: "article" } },
  ]);
});

it("pulses only after a successful like, not initial engagement or a failed write", async () => {
  const { EngagementProvider } = await import("@/components/article-engagement");
  account.user = {
    user_id: "user",
    csrf_token: "csrf",
    name: null,
    email: null,
    expires_at: 4102444800,
  };
  const value = { article_id: "article", opens: 1, likes: 0, liked: false };
  vi.mocked(userRequest).mockResolvedValueOnce([value]);
  Object.defineProperty(Element.prototype, "animate", {
    configurable: true,
    writable: true,
    value: () => {},
  });
  const animate = vi
    .spyOn(Element.prototype, "animate")
    .mockReturnValue({ cancel: vi.fn() } as unknown as Animation);
  try {
    render(
      <EngagementProvider articleIds={["article"]}>
        <ArticleEngagement articleId="article" articleSlug="article-slug" />
      </EngagementProvider>,
    );
    await screen.findByRole("button", { name: "Like article, 0 likes" });
    expect(animate).not.toHaveBeenCalled();
    vi.mocked(userRequest).mockRejectedValueOnce(new Error("Offline"));
    fireEvent.click(screen.getByRole("button", { name: "Like article, 0 likes" }));
    await screen.findByRole("alert");
    expect(animate).not.toHaveBeenCalled();
    vi.mocked(userRequest).mockResolvedValueOnce({ ...value, likes: 1, liked: true });
    fireEvent.click(screen.getByRole("button", { name: "Like article, 0 likes" }));
    await screen.findByRole("button", { name: "Unlike article, 1 likes" });
    expect(animate).toHaveBeenCalledOnce();
  } finally {
    animate.mockRestore();
    Reflect.deleteProperty(Element.prototype, "animate");
  }
});

it("syncs bookmark controls across providers and preserves likes", async () => {
  const { ArticleBookmarkButton, EngagementProvider } =
    await import("@/components/article-engagement");
  account.user = {
    user_id: "user",
    csrf_token: "csrf",
    name: null,
    email: null,
    expires_at: 4102444800,
  };
  vi.mocked(userRequest).mockResolvedValue([
    { article_id: "article", likes: 2, opens: 3, liked: false, bookmarked: false },
  ]);
  render(
    <>
      {[0, 1].map((key) => (
        <EngagementProvider key={key} articleIds={["article"]}>
          <ArticleBookmarkButton articleId="article" articleSlug="article-slug" />
          <ArticleEngagement articleId="article" articleSlug="article-slug" />
        </EngagementProvider>
      ))}
    </>,
  );
  await waitFor(() =>
    expect(
      screen
        .getAllByRole("button", { name: "Save article for later" })
        .every((b) => !(b as HTMLButtonElement).disabled),
    ).toBe(true),
  );
  vi.mocked(userRequest).mockResolvedValueOnce({ article_id: "article", bookmarked: true });
  fireEvent.click(screen.getAllByRole("button", { name: "Save article for later" })[0]);
  await waitFor(() =>
    expect(screen.getAllByRole("button", { name: "Remove bookmark" })).toHaveLength(2),
  );
  expect(userRequest).toHaveBeenLastCalledWith(
    "articles/article/bookmark",
    expect.objectContaining({
      method: "PUT",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": "csrf" },
      body: JSON.stringify({ bookmarked: true }),
    }),
  );
  expect(screen.getAllByRole("button", { name: "Like article, 2 likes" })).toHaveLength(2);
  vi.mocked(userRequest).mockRejectedValueOnce(new Error("Offline"));
  fireEvent.click(screen.getAllByRole("button", { name: "Remove bookmark" })[0]);
  await screen.findByRole("alert");
  expect(screen.getAllByRole("button", { name: "Remove bookmark" })).toHaveLength(2);
  vi.mocked(userRequest).mockResolvedValueOnce({ article_id: "article", bookmarked: false });
  fireEvent.click(screen.getAllByRole("button", { name: "Remove bookmark" })[0]);
  await waitFor(() =>
    expect(screen.getAllByRole("button", { name: "Save article for later" })).toHaveLength(2),
  );
});
