import "./newtab.css";
import { startExtensionAnalytics } from "./analytics";
import { createRoot } from "react-dom/client";
import { useEffect, useState } from "react";
import { FeedContent, type FeedContentProps } from "../../web/src/components/feed-content";
import { useUser } from "../../web/src/components/user-account";
import { UserShell } from "../../web/src/components/user-shell";
import { ThemePreferencesProvider } from "../../web/src/components/theme-preferences";
import { FeedPreferencesProvider } from "../../web/src/components/feed-preferences";
import { ArticleNavigationProvider } from "../../web/src/components/article-navigation";
import { SourceFollowsProvider } from "../../web/src/components/source-follow";
import { SearchResults, SearchFailure } from "../../web/src/components/search-results";
import { SearchFilters } from "../../web/src/components/search-filters";
import { LoadingSkeleton } from "../../web/src/components/loading-skeleton";
import { configureReaderRuntime, readerRequest } from "../../web/src/lib/reader-runtime";
import {
  contentTypeFromRoute,
  contentTypes,
  feedParams,
  parseFilters,
} from "../../web/src/lib/feed-query";
import { parseSearchOptions, normalizeSearch, type SearchResponse } from "../../web/src/lib/search";
import type { FeedPage, FeedOptions, Topic, Source } from "../../web/src/lib/types";
import { createReaderTransport, publicOrigin } from "./transport";
import { linkDestination, useRoute, useRouter } from "./navigation";
import { LocalPage } from "./pages";
import { catalogItem } from "./catalog";
import { Preview } from "./articles";
import { rememberArticles, rememberTopics } from "./public-cache";
import { AccountSession, signedOut } from "./session";
import { PersonalFeed } from "../../web/src/components/personal-feed";
import { ReadLater } from "../../web/src/components/read-later";
import { NotificationPreferencesProvider } from "../../web/src/components/notification-preferences-provider";

configureReaderRuntime({
  request: createReaderTransport(fetch),
  signedOut,
  reload: (href) => {
    window.history.replaceState(null, "", linkDestination(href));
    window.location.reload();
  },
  rememberArticles,
  publicOrigin,
  location: () => new URL(window.location.hash.slice(1) || "/", publicOrigin),
});

async function read<T>(path: string, signal: AbortSignal): Promise<T> {
  const response = await readerRequest(path, { signal });
  if (!response.ok) throw new Error("Reader unavailable");
  return response.json();
}

function Reader({ route }: { route: string }) {
  const { user, loading: sessionLoading } = useUser();
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const refresh = () => setRevision((value) => value + 1);
    window.addEventListener("devfeed:extension-refresh", refresh);
    return () => window.removeEventListener("devfeed:extension-refresh", refresh);
  }, []);
  const key = `${route}:${revision}:${sessionLoading ? "loading" : (user?.user_id ?? "guest")}:${user?.csrf_token ?? ""}`;
  const url = new URL(route, publicOrigin);
  const search = url.pathname === "/search";
  const personal = url.pathname === "/";
  const router = useRouter();
  useEffect(() => {
    if (!sessionLoading && !user && personal) router.replace("/latest");
  }, [sessionLoading, user, personal, router]);
  const bookmarks = url.pathname === "/read-later";
  const detail = /^\/(topics|sources)\/([^/]+)(?:\/([^/]+))?$/.exec(url.pathname);
  const filters = parseFilters({
    ...Object.fromEntries(url.searchParams),
    content_type:
      contentTypeFromRoute(detail?.[3] ?? url.pathname.slice(1)) ??
      url.searchParams.get("content_type") ??
      "",
  });
  const query = normalizeSearch(url.searchParams.get("q") ?? "");
  const searchOptions = parseSearchOptions(url.searchParams);
  const [state, setState] = useState<{
    key: string;
    feed?: FeedContentProps;
    search?: SearchResponse | null;
  }>();
  useEffect(() => {
    if (sessionLoading || personal || bookmarks) return;
    const controller = new AbortController();
    const { signal } = controller;
    async function load() {
      if (search) {
        const result = query
          ? await read<SearchResponse>(`/api/v1/search?${url.searchParams}`, signal).catch(
              () => null,
            )
          : null;
        if (!signal.aborted) setState({ key, search: result });
        return;
      }
      let item: Source | Topic | undefined;
      let resolvedFilters = filters;
      if (detail) {
        try {
          const kind = detail[1] as "topics" | "sources";
          item = await catalogItem(kind, detail[2], signal);
          if (!item) throw new Error("Not found");
          resolvedFilters =
            kind === "topics"
              ? { ...filters, topic: item.slug }
              : { ...filters, source_id: item.id, source_slug: item.slug };
        } catch (reason) {
          if (!signal.aborted)
            setState({
              key,
              feed: {
                filters,
                feed: { status: "rejected", reason },
                topics: { status: "fulfilled", value: [] },
                options: { status: "rejected", reason },
              },
            });
          return;
        }
      }
      const params = feedParams(resolvedFilters);
      const [feed, topics, options] = await Promise.allSettled([
        read<FeedPage>(`/api/v1/feed?${params}`, signal),
        read<{ items: Topic[] }>("/api/v1/topics", signal).then((page) => {
          rememberTopics(page.items);
          return page.items.slice(0, 12);
        }),
        read<FeedOptions>(`/api/v1/feed/options?${params}`, signal).catch(async () => {
          // Compatibility with deployments predating the public options route.
          const { items: sources } = await read<{ items: Source[] }>("/api/v1/sources", signal);
          return {
            sources,
            content_types: [...contentTypes],
            languages: ["en", "es", "fr", "de", "pt", "ja", "zh", "ko", "hi"],
          };
        }),
      ]);
      if (!signal.aborted)
        setState({
          key,
          feed: {
            feed,
            topics,
            options,
            filters: resolvedFilters,
            title: item?.name,
            description: item?.description,
            logoUrl: item?.logo_url,
            section: detail ? (detail[1] as "topics" | "sources") : "feed",
          },
        });
    }
    void load();
    return () => controller.abort();
    // The route and refresh revision fully describe this request.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  if (personal && !user)
    return (
      <UserShell section="personal">
        <LoadingSkeleton label="Loading your feed…" />
      </UserShell>
    );
  if (personal || bookmarks)
    return (
      <UserShell section={personal ? "personal" : "bookmarks"}>
        {personal ? (
          <PersonalFeed
            key={user?.user_id}
            refreshKey={revision}
            cursor={url.searchParams.get("cursor") ?? undefined}
          />
        ) : (
          <ReadLater key={key} cursor={url.searchParams.get("cursor") ?? undefined} />
        )}
      </UserShell>
    );

  if (search)
    return (
      <UserShell section="search" searchQuery={query}>
        <div className="page-heading search-heading">
          <h1>{query ? `Results for “${query}”` : "Search DevFeed"}</h1>
          {!query && <p>Find articles, topics, sources, and tags in one place.</p>}
        </div>
        <SearchFilters key={`filters:${route}`} query={query} options={searchOptions} />
        {query &&
          (state?.key !== key ? (
            <LoadingSkeleton label="Searching DevFeed…" />
          ) : state.search ? (
            <SearchResults key={route} result={state.search} options={searchOptions} />
          ) : (
            <SearchFailure />
          ))}
      </UserShell>
    );
  return state?.key === key && state.feed ? (
    <FeedContent key={key} {...state.feed} />
  ) : (
    <UserShell filters={filters}>
      <LoadingSkeleton label="Loading articles…" />
    </UserShell>
  );
}

function ExtensionReader() {
  const route = useRoute();
  const pathname = new URL(route, publicOrigin).pathname;
  if (
    pathname.startsWith("/settings") ||
    ["/topics", "/sources", "/sources/suggest"].includes(pathname)
  )
    return <LocalPage key={route} route={route} />;
  const article = /^\/articles\/([a-z0-9][a-z0-9-]{0,199})$/i.exec(pathname);
  const background = article ? window.history.state?.readerBackground : undefined;
  return (
    <>
      <Reader route={article ? (background ?? "/latest") : route} />
      {article && <Preview slug={article[1]} direct={!background} />}
    </>
  );
}

startExtensionAnalytics();

createRoot(document.getElementById("root")!).render(
  <AccountSession>
    <ThemePreferencesProvider>
      <NotificationPreferencesProvider>
        <FeedPreferencesProvider>
          <ArticleNavigationProvider>
            <SourceFollowsProvider>
              <ExtensionReader />
            </SourceFollowsProvider>
          </ArticleNavigationProvider>
        </FeedPreferencesProvider>
      </NotificationPreferencesProvider>
    </ThemePreferencesProvider>
  </AccountSession>,
);
