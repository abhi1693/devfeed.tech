import { anonymousFeedDestination } from "../../web/src/lib/attribution";
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
import {
  SearchResults,
  SearchFailure,
  SearchLoading,
} from "../../web/src/components/search-results";
import { SearchFilters } from "../../web/src/components/search-filters";
import { LoadingSkeleton } from "../../web/src/components/loading-skeleton";
import { configureReaderRuntime, readerRequest } from "../../web/src/lib/reader-runtime";
import {
  contentTypes,
  feedParams,
  latestFeedParams,
  parseFilters,
} from "../../web/src/lib/feed-query";
import { parseSearchOptions, normalizeSearch, type SearchResponse } from "../../web/src/lib/search";
import type { FeedPage, FeedOptions, Topic, Source } from "../../web/src/lib/types";
import { createReaderTransport, publicOrigin } from "./transport";
import { linkDestination, useRoute, useRouter } from "./navigation";
import { LocalPage } from "./pages";
import { catalogItem } from "./catalog";
import { Preview } from "./articles";
import { rememberArticles } from "./public-cache";
import { AccountSession, signedOut } from "./session";
import { PersonalFeed } from "../../web/src/components/personal-feed";
import { ReadLater } from "../../web/src/components/read-later";
import { NotificationPreferencesProvider } from "../../web/src/components/notification-preferences-provider";
import { SignupNudge } from "../../web/src/components/signup-nudge";
import { extensionRoute, type ExtensionRoute } from "./routes";

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

function Reader({
  route,
  match,
}: {
  route: string;
  match: Extract<ExtensionRoute, { type: "reader" }>;
}) {
  const { user, loading: sessionLoading } = useUser();
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const refresh = () => setRevision((value) => value + 1);
    window.addEventListener("devfeed:extension-refresh", refresh);
    return () => window.removeEventListener("devfeed:extension-refresh", refresh);
  }, []);
  const key = `${route}:${revision}:${sessionLoading ? "loading" : (user?.user_id ?? "guest")}:${user?.csrf_token ?? ""}`;
  const url = new URL(route, publicOrigin);
  const search = match.page === "search";
  const personal = match.page === "personal";
  const router = useRouter();
  useEffect(() => {
    if (!sessionLoading && !user && personal) {
      const params = new URL(route, publicOrigin).searchParams;
      const query = Object.fromEntries(
        [...new Set(params.keys())].map((key) => {
          const values = params.getAll(key);
          return [key, values.length === 1 ? values[0] : values];
        }),
      );
      router.replace(anonymousFeedDestination(query));
    }
  }, [sessionLoading, user, personal, router, route]);
  const bookmarks = match.page === "bookmarks";
  const detail = match.detail;
  const parsedFilters = parseFilters({
    ...Object.fromEntries(url.searchParams),
    content_type:
      detail?.contentType ?? match.contentType ?? url.searchParams.get("content_type") ?? "",
  });
  const filters =
    url.pathname === "/latest" &&
    !sessionLoading &&
    !user &&
    !parsedFilters.content_type &&
    !parsedFilters.topic &&
    !parsedFilters.source_id &&
    !parsedFilters.tag
      ? { ...parsedFilters, content_type: "article" }
      : parsedFilters;
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
          const kind = detail.kind;
          item = await catalogItem(kind, detail.slug, signal);
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
                options: { status: "rejected", reason },
              },
            });
          return;
        }
      }
      const params = feedParams(resolvedFilters);
      const [feed, options] = await Promise.allSettled([
        read<FeedPage>(`/api/v1/feed?${latestFeedParams(resolvedFilters)}`, signal),
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
            options,
            filters: resolvedFilters,
            title: item?.name,
            topicId: detail?.kind === "topics" ? item?.id : undefined,
            description: item?.description,
            logoUrl: item?.logo_url,
            section: detail?.kind ?? "feed",
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
            filters={parseFilters(Object.fromEntries(url.searchParams))}
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
        <SearchFilters
          key={`filters:${route}`}
          query={query}
          options={searchOptions}
          loading={!!query && state?.key !== key}
        />
        {query &&
          (state?.key !== key ? (
            <SearchLoading />
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
  const match = extensionRoute(pathname);
  if (match?.type === "local") return <LocalPage key={route} route={route} />;
  const article = match?.type === "article" ? match : undefined;
  const background = article ? window.history.state?.readerBackground : undefined;
  const readerRoute = article ? (background ?? "/latest") : route;
  // A preview changes the foreground URL, not the kind of reader retained behind it.
  const backgroundMatch = extensionRoute(new URL(readerRoute, publicOrigin).pathname);
  const readerMatch: Extract<ExtensionRoute, { type: "reader" }> =
    backgroundMatch?.type === "reader" ? backgroundMatch : { type: "reader", page: "feed" };
  return (
    <>
      <Reader route={readerRoute} match={readerMatch} />
      {article && <Preview slug={article.slug} direct={!background} />}
      <SignupNudge pathname={pathname} />
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
