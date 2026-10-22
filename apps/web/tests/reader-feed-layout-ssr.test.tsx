// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ArticleGrid } from "@/components/article-grid";
import { FeedPreferencesProvider } from "@/components/feed-preferences";
import { FeedLayoutMenu } from "@/components/feed-layout-menu";
import { JsonLd } from "@/components/json-ld";
import { ReaderFeedLayout } from "@/components/reader-feed-layout";
import Link from "@/components/reader-link";
import { readerRequest } from "@/lib/reader-runtime";
import { userRequest, type UserIdentity } from "@/lib/user";
import type { FeedViewScope } from "@/lib/feed-view-scope";
import { article } from "./fixtures";

const session = vi.hoisted(() => ({
  user: null as UserIdentity | null,
  loading: false,
  sessionRevision: 1,
  profile: null,
  refreshProfile: vi.fn(),
}));
vi.mock("@/components/user-account", () => ({ useUser: () => session }));
vi.mock("@/lib/user", () => ({ userRequest: vi.fn() }));
vi.mock("@/lib/reader-runtime", async (original) => ({
  ...(await original<typeof import("@/lib/reader-runtime")>()),
  readerRequest: vi.fn(),
}));
vi.mock("@/components/columns-workspace", () => ({
  ColumnsWorkspace: () => <section aria-label="Columns workspace" />,
}));

const topicId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const account: UserIdentity = {
  user_id: "reader-a",
  name: "Reader",
  email: null,
  csrf_token: "csrf-reader-a",
  expires_at: 4102444800,
};

function InitialFeed() {
  return (
    <section aria-label="Public feed">
      <FeedLayoutMenu />
      <input aria-label="Retained feed state" defaultValue="initial" />
      <ArticleGrid articles={[article]} />
      <Link href="/latest?cursor=next-page" rel="next">
        Next page
      </Link>
      <JsonLd
        data={{ "@context": "https://schema.org", "@type": "ItemList", name: article.title }}
      />
    </section>
  );
}

function PublicFeed({
  scope,
  selection,
}: {
  scope?: FeedViewScope;
  selection?: { kind: "topics" | "tags"; slug: string };
}) {
  return (
    <FeedPreferencesProvider>
      <ReaderFeedLayout scope={scope} selection={selection}>
        <InitialFeed />
      </ReaderFeedLayout>
    </FeedPreferencesProvider>
  );
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

beforeEach(() => {
  localStorage.clear();
  session.user = null;
  session.loading = false;
  vi.mocked(readerRequest).mockReset();
  vi.mocked(userRequest).mockReset();
});
afterEach(cleanup);

it.each(["latest", "trending", "content:article", `topic:${topicId}`] as FeedViewScope[])(
  "server-renders public article links, pagination, and structured data while %s preferences load",
  (scope) => {
    const html = renderToStaticMarkup(<PublicFeed scope={scope} />);
    const initial = new DOMParser().parseFromString(html, "text/html");
    expect(initial.querySelector(`a[href="/articles/${article.slug}"]`)?.textContent).toBe(
      article.title,
    );
    expect(initial.querySelector(".article-card")).not.toBeNull();
    expect(initial.querySelector('a[rel="next"]')?.getAttribute("href")).toBe(
      "/latest?cursor=next-page",
    );
    expect(
      JSON.parse(initial.querySelector('script[type="application/ld+json"]')!.textContent!),
    ).toMatchObject({
      "@type": "ItemList",
      name: article.title,
    });
    expect(html).not.toContain("Loading feed layout");
    expect(userRequest).not.toHaveBeenCalled();
    expect(readerRequest).not.toHaveBeenCalled();
  },
);

it.each(["topics", "tags"] as const)(
  "server-renders public Grid before an unresolved %s alias can run browser effects",
  (kind) => {
    const html = renderToStaticMarkup(<PublicFeed selection={{ kind, slug: "public-alias" }} />);
    const initial = new DOMParser().parseFromString(html, "text/html");
    expect(initial.querySelector(`a[href="/articles/${article.slug}"]`)).not.toBeNull();
    expect(initial.querySelector(".article-card")).not.toBeNull();
    expect(initial.querySelector('a[rel="next"]')).not.toBeNull();
    expect(initial.querySelector('script[type="application/ld+json"]')).not.toBeNull();
    expect(readerRequest).not.toHaveBeenCalled();
    expect(userRequest).not.toHaveBeenCalled();
  },
);

it("keeps authenticated public feeds visible while their account layout loads", async () => {
  session.user = account;
  const layout = deferred<{ view: "stacked" }>();
  vi.mocked(userRequest).mockImplementation(async (path) => {
    if (path === "settings/feed")
      return { view: "cards", columns: [], languages: ["en"], content_types: ["article"] };
    if (path === "settings/feed-view?feed=latest") return layout.promise;
    if (path.startsWith("engagement?")) return [];
    throw new Error(`Unexpected request: ${path}`);
  });
  const app = render(<PublicFeed scope="latest" />);
  const feed = screen.getByRole("region", { name: "Public feed" });
  await waitFor(() =>
    expect(userRequest).toHaveBeenCalledWith("settings/feed-view?feed=latest", expect.anything()),
  );
  expect(feed.querySelector(".article-card")).not.toBeNull();
  expect(screen.getByRole("link", { name: "Next page" })).toBeTruthy();
  await act(async () => layout.resolve({ view: "stacked" }));
  expect(screen.getByRole("region", { name: "Public feed" })).toBe(feed);
  expect(feed.querySelector(".article-stacked-row")).not.toBeNull();
  app.unmount();
});

it("retains the public feed through alias loading, error, retry, and resolved account preferences", async () => {
  session.user = account;
  const first = deferred<Response>();
  const retry = deferred<Response>();
  const layout = deferred<{ view: "cards" }>();
  vi.mocked(readerRequest).mockReturnValueOnce(first.promise).mockReturnValueOnce(retry.promise);
  vi.mocked(userRequest).mockImplementation(async (path) => {
    if (path === "settings/feed")
      return { view: "cards", columns: [], languages: ["en"], content_types: ["article"] };
    if (path === `settings/feed-view?feed=topic%3A${topicId}`) return layout.promise;
    if (path.startsWith("engagement?")) return [];
    throw new Error(`Unexpected request: ${path}`);
  });
  render(<PublicFeed selection={{ kind: "topics", slug: "public-alias" }} />);
  const feed = screen.getByRole("region", { name: "Public feed" });
  const draft = screen.getByRole("textbox", { name: "Retained feed state" });
  const title = feed.querySelector(`a[href="/articles/${article.slug}"]`);
  fireEvent.change(draft, { target: { value: "reading state" } });
  await waitFor(() => expect(userRequest).toHaveBeenCalledWith("settings/feed", expect.anything()));
  expect(
    vi.mocked(userRequest).mock.calls.some(([path]) => path.startsWith("settings/feed-view")),
  ).toBe(false);
  expect(screen.queryByRole("button", { name: /^Change feed layout/ })).toBeNull();
  await act(async () => first.reject(new Error("Catalog unavailable")));
  expect(screen.getByText("Couldn’t load this feed’s layout.")).toBeTruthy();
  expect(screen.getByRole("region", { name: "Public feed" })).toBe(feed);
  expect(feed.querySelector(`a[href="/articles/${article.slug}"]`)).toBe(title);
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  expect(screen.getByRole("region", { name: "Public feed" })).toBe(feed);
  await act(async () => retry.resolve(Response.json({ id: topicId })));
  await waitFor(() =>
    expect(userRequest).toHaveBeenCalledWith(
      `settings/feed-view?feed=topic%3A${topicId}`,
      expect.anything(),
    ),
  );
  expect(feed.querySelector(`a[href="/articles/${article.slug}"]`)).toBe(title);
  await act(async () => layout.resolve({ view: "cards" }));
  expect(screen.getByRole("region", { name: "Public feed" })).toBe(feed);
  expect(feed.querySelector(`a[href="/articles/${article.slug}"]`)).toBe(title);
  expect(screen.getByRole("textbox", { name: "Retained feed state" })).toBe(draft);
  expect(draft).toHaveProperty("value", "reading state");
  expect(userRequest).not.toHaveBeenCalledWith("settings/feed-view?feed=latest", expect.anything());
  expect(vi.mocked(userRequest).mock.calls.every(([, init]) => !init?.method)).toBe(true);
});

it("keeps the private My feed workspace blocked until its own preference is available", () => {
  session.user = account;
  const html = renderToStaticMarkup(
    <FeedPreferencesProvider>
      <ReaderFeedLayout scope="my_feed" workspace="my_feed" allowColumns>
        <p>Private feed content</p>
      </ReaderFeedLayout>
    </FeedPreferencesProvider>,
  );
  expect(html).toContain("Loading feed layout");
  expect(html).not.toContain("Private feed content");
  expect(html).not.toContain('aria-label="Columns workspace"');
  expect(userRequest).not.toHaveBeenCalled();
});
