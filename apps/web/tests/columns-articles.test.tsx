// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { ColumnsWorkspace } from "@/components/columns-workspace";
import { FeedPreferencesProvider } from "@/components/feed-preferences";
import { UserProvider } from "@/components/user-account";
import { ArticleNavigationProvider, useArticleNavigation } from "@/components/article-navigation";
import { guestReaderPreferencesKey, type ReaderColumn } from "@/lib/reader-preferences";
import type { Article } from "@/lib/types";
import type { UserIdentity } from "@/lib/user";
import { observePartnerPlacement } from "@/lib/partner-tracking";
import {
  validPlacementQuery,
  type PartnerPlacement,
  type PlacementQuery,
} from "@/lib/partner-placements";
import { article } from "./fixtures";
import { partnerReceipt, sponsoredPlacement } from "./partner-placement-fixtures";

vi.mock("@/components/settings-toast", () => ({ useSettingsToast: () => vi.fn() }));
vi.mock("@/lib/partner-tracking", () => ({ observePartnerPlacement: vi.fn(() => () => {}) }));
const uuid = (index: number) => `aaaaaaaa-aaaa-4aaa-8aaa-${index.toString(16).padStart(12, "0")}`;
const user: UserIdentity = {
  user_id: "reader-a",
  csrf_token: "csrf-a",
  expires_at: 4102444800,
  name: "Reader A",
  email: null,
};
const latest: ReaderColumn = { id: uuid(1), feed: { kind: "latest" } };
const source: ReaderColumn = { id: uuid(2), feed: { kind: "source", id: uuid(102) } };
const recommended: ReaderColumn = { id: uuid(3), feed: { kind: "recommended" } };
const topic: ReaderColumn = { id: uuid(4), feed: { kind: "topic", id: uuid(104) } };
const tag: ReaderColumn = { id: uuid(5), feed: { kind: "tag", id: uuid(105) } };
const shared = {
  ...article,
  id: uuid(11),
  slug: "shared-article",
  title: "Shared article",
  content_type: "news",
};
const publicOnly = {
  ...article,
  id: uuid(12),
  slug: "public-article",
  title: "Public article",
  content_type: "news",
};
const sourceOnly = {
  ...article,
  id: uuid(13),
  slug: "source-article",
  title: "Source article",
  content_type: "news",
};
const privateOnly = {
  ...article,
  id: uuid(14),
  slug: "private-article",
  title: "Private recommendation",
  content_type: "news",
};
type FeedKind = "latest" | "source" | "recommended" | "topic" | "tag";
type FeedRead = { kind: FeedKind; url: URL; signal: AbortSignal | null | undefined };
type PlacementRead = { query: PlacementQuery; init: RequestInit | undefined };
const page = (items: Article[], next_cursor: string | null = null) =>
  Response.json({ items, next_cursor, status: "ready", has_interests: true, reasons: {} });
const batch = (start: number, count = 4): Article[] =>
  Array.from({ length: count }, (_, index) => ({
    ...shared,
    id: uuid(2000 + start + index),
    slug: `column-article-${start + index}`,
    title: `Column article ${start + index}`,
  }));
const product = (): PartnerPlacement => ({
  ...sponsoredPlacement,
  receipt: partnerReceipt({ assetId: sponsoredPlacement.asset_id }),
  attribution: "Paid promotion by Example partner",
  link_rel: ["sponsored", "external"],
  image_url: "https://images.example.test/product.webp",
  image_variants: [{ url: "https://images.example.test/product-320.webp", width: 320 }],
});

function transport(
  columns: ReaderColumn[],
  feed: (request: FeedRead) => Promise<Response> | Response,
  initialUser: UserIdentity | null = user,
  placements: (query: PlacementQuery) => Promise<Response> | Response = () =>
    Response.json({ items: [] }),
) {
  let identity = initialUser;
  let pendingAuth: Promise<Response> | null = null;
  let pendingSource: Promise<Response> | null = null;
  let settings = { view: "columns", columns, languages: ["en"], content_types: ["news"] };
  const reads: FeedRead[] = [];
  const placementReads: PlacementRead[] = [];
  const engagement = new Map<
    string,
    { article_id: string; likes: number; opens: number; liked: boolean; bookmarked: boolean }
  >();
  const stats = (id: string) => {
    if (!engagement.has(id))
      engagement.set(id, { article_id: id, likes: 2, opens: 5, liked: false, bookmarked: false });
    return engagement.get(id)!;
  };
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), window.location.href);
    if (url.pathname.endsWith("/auth/me")) return pendingAuth ?? Response.json(identity);
    if (url.pathname.endsWith("/settings/profile"))
      return Response.json({ display_name: null, avatar_url: null });
    if (url.pathname.endsWith("/settings/feed")) {
      if (init?.method === "PUT") settings = JSON.parse(String(init.body));
      return Response.json(settings);
    }
    if (url.pathname.endsWith("/settings/columns")) {
      expect(url.searchParams.get("workspace")).toBe("my_feed");
      if (init?.method === "PUT")
        settings = { ...settings, columns: JSON.parse(String(init.body)).columns };
      return Response.json({ initialized: true, columns: settings.columns });
    }
    if (url.pathname === `/api/v1/sources/${uuid(102)}`)
      return (
        pendingSource ??
        Response.json({
          id: uuid(102),
          name: "Rust Blog",
          slug: "rust-blog",
          website_url: null,
          logo_url: null,
          description: null,
        })
      );
    if (
      url.pathname === `/api/v1/topics/${uuid(104)}` ||
      url.pathname === `/api/v1/tags/${uuid(105)}`
    ) {
      expect(url.searchParams.get("by_id")).toBe("true");
      return Response.json({
        id: url.pathname.includes("/topics/") ? uuid(104) : uuid(105),
        name: "Type safety",
        slug: "type-safety",
        description: null,
      });
    }
    if (url.pathname === "/api/v1/feed" || url.pathname === "/api/v1/user/feed") {
      const request: FeedRead = {
        kind: url.pathname.includes("/user/")
          ? "recommended"
          : url.searchParams.has("source_id")
            ? "source"
            : url.searchParams.has("topic")
              ? "topic"
              : url.searchParams.has("tag")
                ? "tag"
                : "latest",
        url,
        signal: init?.signal,
      };
      reads.push(request);
      return feed(request);
    }
    if (url.pathname === "/api/v1/user/engagement")
      return Response.json(url.searchParams.getAll("article_id").map(stats));
    if (url.pathname.endsWith("/partner-placements/query")) {
      const query: unknown = JSON.parse(String(init?.body));
      expect(validPlacementQuery(query)).toBe(true);
      if (!validPlacementQuery(query)) throw new Error("Invalid placement context");
      placementReads.push({ query, init });
      return placements(query);
    }
    const action = url.pathname.match(/^\/api\/v1\/user\/articles\/([^/]+)\/(bookmark|like|open)$/);
    if (action) {
      const value = stats(action[1]);
      const payload = init?.body ? JSON.parse(String(init.body)) : {};
      if (action[2] === "bookmark") value.bookmarked = payload.bookmarked;
      if (action[2] === "like") {
        value.likes += payload.liked ? 1 : -1;
        value.liked = payload.liked;
      }
      if (action[2] === "open") value.opens++;
      return Response.json(value);
    }
    throw new Error(`Unexpected Columns request: ${url.pathname}`);
  });
  vi.stubGlobal("fetch", fetcher);
  return {
    reads,
    placementReads,
    fetcher,
    deferSourceLookup() {
      let resolve!: (response: Response) => void;
      pendingSource = new Promise<Response>((done) => {
        resolve = done;
      });
      return async () => {
        await act(async () => {
          pendingSource = null;
          resolve(
            Response.json({
              id: uuid(102),
              name: "Rust Blog",
              slug: "rust-blog",
              website_url: null,
              logo_url: null,
              description: null,
            }),
          );
        });
      };
    },
    deferAuth() {
      let resolve!: (response: Response) => void;
      pendingAuth = new Promise<Response>((done) => {
        resolve = done;
      });
      return async (next: UserIdentity | null) => {
        await act(async () => {
          identity = next;
          pendingAuth = null;
          resolve(Response.json(next));
        });
      };
    },
  };
}

function Sequence() {
  const { sequence } = useArticleNavigation();
  return <output aria-label="Reading sequence">{sequence?.slugs.join(",")}</output>;
}
function workspace() {
  return render(
    <UserProvider>
      <FeedPreferencesProvider>
        <ArticleNavigationProvider>
          <ColumnsWorkspace workspace="my_feed" />
          <Sequence />
        </ArticleNavigationProvider>
      </FeedPreferencesProvider>
    </UserProvider>,
  );
}
function row(columnId: string, articleId: string) {
  return document.querySelector<HTMLElement>(
    `[data-column-id="${columnId}"] [data-article-id="${articleId}"]`,
  )!;
}
function preview(link: HTMLElement) {
  link.addEventListener("click", (event) => event.preventDefault(), { once: true });
  fireEvent.click(link);
}
beforeEach(() => {
  localStorage.clear();
  window.history.replaceState(null, "", "/");
  vi.mocked(observePartnerPlacement).mockClear();
  const bounds = HTMLElement.prototype.getBoundingClientRect;
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (
    this: HTMLElement,
  ) {
    const column = this.closest<HTMLElement>("[data-column-id]");
    if (!column) return bounds.call(this);
    const index = [...document.querySelectorAll("[data-column-id]")].indexOf(column);
    return new DOMRect(24 + index * 336, 120, 320, 640);
  });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

it("shares duplicate article actions across columns and activates the clicked column's reading sequence", async () => {
  const api = transport([latest, source], ({ kind }) =>
    kind === "source" ? page([sourceOnly, shared]) : page([shared, publicOnly]),
  );
  workspace();
  await screen.findByRole("link", { name: sourceOnly.title });
  await screen.findByRole("link", { name: publicOnly.title });
  const original = row(latest.id, shared.id);
  const duplicate = row(source.id, shared.id);
  await waitFor(() =>
    expect(within(original).getByRole("button", { name: "Save article for later" })).toHaveProperty(
      "disabled",
      false,
    ),
  );
  fireEvent.click(within(original).getByRole("button", { name: "Save article for later" }));
  await waitFor(() =>
    expect(screen.getAllByRole("button", { name: "Remove bookmark" })).toHaveLength(2),
  );
  fireEvent.click(within(duplicate).getByRole("button", { name: "Like article, 2 likes" }));
  await waitFor(() =>
    expect(screen.getAllByRole("button", { name: "Unlike article, 3 likes" })).toHaveLength(2),
  );
  preview(screen.getByRole("link", { name: sourceOnly.title }));
  expect(screen.getByLabelText("Reading sequence").textContent).toBe(
    `${sourceOnly.slug},${shared.slug}`,
  );
  preview(screen.getByRole("link", { name: publicOnly.title }));
  expect(screen.getByLabelText("Reading sequence").textContent).toBe(
    `${shared.slug},${publicOnly.slug}`,
  );
  const keyboard = userEvent.setup();
  const dragHandle = screen.getByRole("button", { name: "Reorder Latest column" });
  dragHandle.focus();
  await keyboard.keyboard("[Space]");
  await waitFor(() => expect(dragHandle.getAttribute("aria-pressed")).toBe("true"));
  await keyboard.keyboard("[ArrowRight]");
  await keyboard.keyboard("[Space]");
  await waitFor(() =>
    expect(
      [...document.querySelectorAll("[data-column-id]")].map((column) =>
        column.getAttribute("data-column-id"),
      ),
    ).toEqual([source.id, latest.id]),
  );
  expect(row(latest.id, shared.id)).toBe(original);
  expect(within(original).getByRole("button", { name: "Remove bookmark" })).toBeTruthy();
  expect(api.reads.filter(({ kind }) => kind === "latest")).toHaveLength(1);
  expect(api.reads.filter(({ kind }) => kind === "source")).toHaveLength(1);
  expect(screen.getByLabelText("Reading sequence").textContent).toBe(
    `${shared.slug},${publicOnly.slug}`,
  );
  expect(document.activeElement).toBe(dragHandle);
  expect(api.fetcher.mock.calls.some(([input]) => String(input).endsWith("/open"))).toBe(false);
});

it("keeps My feed columns free of article thumbnails and publisher logos while retaining attribution", async () => {
  const publisher = {
    ...article.sources[0],
    logo_url: "https://images.example.test/publisher.webp",
  };
  const item = {
    ...shared,
    image_url: "https://images.example.test/article.webp",
    image_variants: [{ url: "https://images.example.test/article-320.webp", width: 320 }],
    publisher,
    sources: [publisher],
  };
  transport([latest], () => page([item]));
  workspace();
  await screen.findByRole("link", { name: item.title });
  const displayed = row(latest.id, item.id);
  expect(displayed.querySelector("img")).toBeNull();
  expect(within(displayed).getByRole("link", { name: publisher.name })).toBeTruthy();
  expect(within(displayed).getByText(item.summary)).toBeTruthy();
  expect(within(displayed).getByRole("button", { name: "Save article for later" })).toBeTruthy();
});

it("retries and paginates one failed column without reloading its neighbor", async () => {
  let sourceReads = 0;
  const api = transport([latest, source], ({ kind, url }) => {
    if (kind === "latest") return page([publicOnly], "latest-next");
    if (++sourceReads === 1) return Response.json({}, { status: 503 });
    return url.searchParams.has("cursor")
      ? page([sourceOnly, shared])
      : page([sourceOnly], "source-next");
  });
  workspace();
  await screen.findByRole("link", { name: publicOnly.title });
  const neighbor = row(latest.id, publicOnly.id);
  const failed = await screen.findByRole("region", { name: "Articles in Rust Blog column" });
  fireEvent.click(await within(failed).findByRole("button", { name: "Try again" }));
  await within(failed).findByRole("link", { name: sourceOnly.title });
  fireEvent.click(within(failed).getByRole("button", { name: "Load more articles" }));
  await within(failed).findByRole("link", { name: shared.title });
  expect(within(failed).getAllByRole("link", { name: sourceOnly.title })).toHaveLength(1);
  expect(row(latest.id, publicOnly.id)).toBe(neighbor);
  expect(api.reads.filter(({ kind }) => kind === "latest")).toHaveLength(1);
  expect(api.reads.filter(({ kind }) => kind === "source")).toHaveLength(3);
  expect(
    within(screen.getByRole("region", { name: "Articles in Latest column" })).getByRole("button", {
      name: "Load more articles",
    }),
  ).toBeTruthy();
});

it("masks private recommendations during an ordinary history check and loads updated confirmed column data", async () => {
  const updatedPrivate = { ...privateOnly, title: "Updated private recommendation" };
  const updatedPublic = { ...publicOnly, title: "Updated public article" };
  let revalidated = false;
  const api = transport([latest, recommended], ({ kind }) =>
    page([
      kind === "recommended"
        ? revalidated
          ? updatedPrivate
          : privateOnly
        : revalidated
          ? updatedPublic
          : publicOnly,
    ]),
  );
  workspace();
  const privateTitle = await screen.findByRole("link", { name: privateOnly.title });
  const publicTitle = await screen.findByRole("link", { name: publicOnly.title });
  const complete = api.deferAuth();
  fireEvent(window, new PopStateEvent("popstate"));
  expect(screen.queryByRole("link", { name: privateOnly.title })).toBeNull();
  expect(privateTitle.isConnected).toBe(true);
  expect(screen.getByRole("link", { name: publicOnly.title })).toBe(publicTitle);
  revalidated = true;
  await complete({ ...user });
  const refreshedPrivate = await screen.findByRole("link", { name: updatedPrivate.title });
  await screen.findByRole("link", { name: updatedPublic.title });
  expect(screen.queryByRole("link", { name: privateOnly.title })).toBeNull();
  expect(screen.queryByRole("link", { name: publicOnly.title })).toBeNull();
  expect(api.reads.filter(({ kind }) => kind === "recommended")).toHaveLength(2);
  expect(api.reads.filter(({ kind }) => kind === "latest")).toHaveLength(2);
  const signOut = api.deferAuth();
  fireEvent(window, new PopStateEvent("popstate"));
  await signOut(null);
  expect(refreshedPrivate.isConnected).toBe(false);
  expect(screen.queryByRole("link", { name: updatedPrivate.title })).toBeNull();
});

it("never loads guest recommendations while public columns remain available", async () => {
  localStorage.setItem(
    guestReaderPreferencesKey,
    JSON.stringify({ version: 1, view: "columns", columns: [latest, recommended] }),
  );
  const api = transport([latest, recommended], () => page([publicOnly]), null);
  workspace();
  await screen.findByRole("link", { name: publicOnly.title });
  const guestColumn = document.querySelector<HTMLElement>(`[data-column-id="${recommended.id}"]`)!;
  expect(within(guestColumn).getByText("Sign in to view recommendations.")).toBeTruthy();
  expect(within(guestColumn).getByRole("link", { name: "Sign in" })).toBeTruthy();
  expect(
    within(guestColumn).queryByRole("heading", { name: "Sign in to view recommendations" }),
  ).toBeNull();
  expect(within(guestColumn).queryByRole("link", { name: "Browse latest articles" })).toBeNull();
  expect(api.reads.filter(({ kind }) => kind === "recommended")).toHaveLength(0);
  expect(api.reads.filter(({ kind }) => kind === "latest")).toHaveLength(1);
});

it("omits header refresh controls and retains articles and the active sequence through column menus", async () => {
  const api = transport([latest, source], ({ kind }) =>
    page([kind === "source" ? sourceOnly : publicOnly]),
  );
  workspace();
  const publicTitle = await screen.findByRole("link", { name: publicOnly.title });
  const sourceTitle = await screen.findByRole("link", { name: sourceOnly.title });
  expect(screen.queryByRole("button", { name: /^Refresh .+ column$/ })).toBeNull();
  const reads = api.reads.length;
  const metadataReads = () =>
    api.fetcher.mock.calls.filter(([input]) =>
      new URL(String(input), window.location.href).pathname.endsWith(`/sources/${uuid(102)}`),
    ).length;
  const lookups = metadataReads();
  preview(publicTitle);
  expect(screen.getByLabelText("Reading sequence").textContent).toBe(publicOnly.slug);
  for (const name of ["Rust Blog", "Latest"]) {
    fireEvent.keyDown(screen.getByRole("button", { name: `Options for ${name} column` }), {
      key: "Enter",
    });
    const menu = await screen.findByRole("menu");
    expect(
      within(menu).getByRole("menuitem", { name: `Change feed for ${name} column` }),
    ).toBeTruthy();
    fireEvent.keyDown(menu, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("menu")).toBeNull());
    expect(screen.getByLabelText("Reading sequence").textContent).toBe(publicOnly.slug);
  }
  preview(sourceTitle);
  expect(screen.getByLabelText("Reading sequence").textContent).toBe(sourceOnly.slug);
  expect(screen.getByRole("link", { name: publicOnly.title })).toBe(publicTitle);
  expect(screen.getByRole("link", { name: sourceOnly.title })).toBe(sourceTitle);
  expect(api.reads).toHaveLength(reads);
  expect(metadataReads()).toBe(lookups);
});

it("delivers a disclosed text-only product without replacing organic rows or adding it to the reading sequence", async () => {
  const articles = batch(0, 24);
  const item = product();
  let deliver!: (response: Response) => void;
  const pending = new Promise<Response>((resolve) => {
    deliver = resolve;
  });
  const api = transport(
    [latest],
    () => page(articles),
    user,
    () => pending,
  );
  workspace();
  const articleLink = await screen.findByRole("link", { name: articles[0].title });
  await waitFor(() => expect(api.placementReads).toHaveLength(1));
  const retained = row(latest.id, articles[0].id);
  preview(articleLink);
  const sequence = articles.map(({ slug }) => slug).join(",");
  expect(screen.getByLabelText("Reading sequence").textContent).toBe(sequence);
  await act(async () => deliver(Response.json({ items: [item] })));
  const link = await screen.findByRole("link", { name: item.name });
  const promotion = link.closest<HTMLElement>("[data-product-id]")!;
  expect(promotion.getAttribute("data-ad-format")).toBe("feed");
  expect(within(promotion).getByText("Sponsored")).toBeTruthy();
  expect(within(promotion).getAllByText(item.attribution!)).toHaveLength(1);
  expect(promotion.querySelector("img")).toBeNull();
  expect(link.getAttribute("href")).toBe(item.url);
  expect(link.getAttribute("target")).toBe("_blank");
  expect(link.getAttribute("rel")?.split(" ")).toEqual(
    expect.arrayContaining(["sponsored", "noopener", "noreferrer"]),
  );
  expect(
    vi
      .mocked(observePartnerPlacement)
      .mock.calls.some(([target, receipt]) => target === link && receipt === item.receipt),
  ).toBe(true);
  preview(link);
  expect(screen.getByLabelText("Reading sequence").textContent).toBe(sequence);
  expect(row(latest.id, articles[0].id)).toBe(retained);
  expect(screen.getByRole("link", { name: articles[0].title })).toBe(articleLink);
  expect(api.reads).toHaveLength(1);
  expect(api.placementReads).toHaveLength(1);
  expect(api.placementReads[0].query.ad_formats).toEqual(["feed"]);
  expect(api.placementReads[0].query).toMatchObject({
    surface: "feed",
    article_ids: articles.map(({ id }) => id),
    allow_sponsored_fallback: true,
  });
  expect(api.placementReads[0].init).toMatchObject({
    method: "POST",
    headers: { "X-CSRF-Token": user.csrf_token },
    cache: "no-store",
    referrerPolicy: "no-referrer",
  });
  expect(api.fetcher.mock.calls.some(([input]) => String(input).endsWith("/open"))).toBe(false);
});

it.each(["ad-free", "unavailable"] as const)(
  "keeps an %s product response independent of article pagination",
  async (delivery) => {
    const initial = batch(0, 24);
    const next = batch(24, 24);
    const api = transport(
      [latest, source],
      ({ kind, url }) =>
        kind === "source"
          ? page([sourceOnly])
          : url.searchParams.has("cursor")
            ? page(next)
            : page(initial, "next-page"),
      user,
      () =>
        delivery === "ad-free" ? Response.json({ items: [] }) : Response.json({}, { status: 503 }),
    );
    workspace();
    await screen.findByRole("link", { name: sourceOnly.title });
    const organic = await screen.findByRole("link", { name: initial[0].title });
    await waitFor(() => expect(api.placementReads).toHaveLength(1));
    const neighbor = row(source.id, sourceOnly.id);
    expect(document.querySelector("[data-product-id]")).toBeNull();
    const column = screen.getByRole("region", { name: "Articles in Latest column" });
    fireEvent.click(within(column).getByRole("button", { name: "Load more articles" }));
    await within(column).findByRole("link", { name: next[0].title });
    expect(screen.getByRole("link", { name: initial[0].title })).toBe(organic);
    expect(row(source.id, sourceOnly.id)).toBe(neighbor);
    expect(api.reads.filter(({ kind }) => kind === "source")).toHaveLength(1);
    expect(api.reads.filter(({ kind }) => kind === "latest")).toHaveLength(2);
    expect(api.placementReads).toHaveLength(1);
    expect(document.querySelector("[data-product-id]")).toBeNull();
  },
);

it("shows a product once when two independent columns receive the same eligible delivery", async () => {
  const item = product();
  const publicArticles = batch(0, 24);
  const sourceArticles = batch(100, 24);
  const api = transport(
    [latest, source],
    ({ kind }) => page(kind === "source" ? sourceArticles : publicArticles),
    user,
    () =>
      Response.json({ items: [{ ...item, receipt: partnerReceipt({ assetId: item.asset_id }) }] }),
  );
  workspace();
  await screen.findByRole("link", { name: publicArticles[0].title });
  await screen.findByRole("link", { name: sourceArticles[0].title });
  await waitFor(() => expect(api.placementReads).toHaveLength(2));
  await screen.findByRole("link", { name: item.name });
  expect(screen.getAllByRole("link", { name: item.name })).toHaveLength(1);
  expect(document.querySelectorAll(`[data-product-id="${item.product_id}"]`)).toHaveLength(1);
  expect(api.reads.filter(({ kind }) => kind === "source")).toHaveLength(1);
  expect(api.reads.filter(({ kind }) => kind === "latest")).toHaveLength(1);
  expect(
    api.placementReads.find(({ query }) => query.article_ids?.[0] === sourceArticles[0].id)?.query
      .allow_sponsored_fallback,
  ).toBe(false);
});

it.each([topic, tag, recommended])(
  "uses the saved $feed.kind selection as placement context",
  async (column) => {
    const articles = batch(0, 24);
    const api = transport([column], () => page(articles));
    workspace();
    await screen.findByRole("link", { name: articles[0].title });
    await waitFor(() => expect(api.placementReads).toHaveLength(1));
    const { query } = api.placementReads[0];
    expect(query.ad_formats).toEqual(["feed"]);
    if (column.feed.kind === "topic") {
      expect(query.surface).toBe("topic");
      expect(query.topic_id).toBe(column.feed.id);
      expect(query).not.toHaveProperty("article_ids");
      expect(query).not.toHaveProperty("allow_sponsored_fallback");
    } else {
      expect(query.surface).toBe("feed");
      expect(query.article_ids).toEqual(articles.map(({ id }) => id));
      expect(query.allow_sponsored_fallback).toBe(column.feed.kind === "recommended");
      expect(query).not.toHaveProperty("topic_id");
    }
  },
);

it("retains the 72-organic-article cap with a product cell without querying placements for appended pages", async () => {
  const item = product();
  const pages = [batch(0, 24), batch(24, 24), batch(48, 24)];
  const api = transport(
    [latest],
    ({ url }) => {
      const pageIndex = Number(url.searchParams.get("cursor") ?? 0);
      return page(pages[pageIndex], String(pageIndex + 1));
    },
    user,
    () => Response.json({ items: [item] }),
  );
  workspace();
  const column = await screen.findByRole("region", { name: "Articles in Latest column" });
  await within(column).findByRole("link", { name: pages[0][0].title });
  await within(column).findByRole("link", { name: item.name });
  for (const pageIndex of [1, 2]) {
    fireEvent.click(within(column).getByRole("button", { name: "Load more articles" }));
    await within(column).findByRole("link", { name: pages[pageIndex][0].title });
  }
  expect(column.querySelectorAll("[data-article-id]")).toHaveLength(72);
  expect(column.querySelectorAll("[data-product-id]")).toHaveLength(1);
  expect(within(column).queryByRole("button", { name: "Load more articles" })).toBeNull();
  expect(within(column).getByText("Showing up to 72 articles in this column.")).toBeTruthy();
  expect(api.reads).toHaveLength(3);
  expect(api.placementReads).toHaveLength(1);
});
