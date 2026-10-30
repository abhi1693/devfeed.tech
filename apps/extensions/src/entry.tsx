import { TagFollowsProvider } from "../../web/src/components/tag-follows";
import { HeaderSponsorshipProvider } from "../../web/src/components/header-sponsorship";
import { TopicFollowsProvider } from "../../web/src/components/topic-follows";
import "./newtab.css";
import { startExtensionAnalytics } from "./analytics";
import { createRoot } from "react-dom/client";
import { useEffect, useState } from "react";
import { FeedContent, type FeedContentProps } from "../../web/src/components/feed-content";
import { GuestHomeIntro } from "../../web/src/components/guest-home-intro";
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
  feedParams,
  searchParamsFromUrl,
  latestFeedParams,
  trendingFeedParams,
} from "../../web/src/lib/feed-query";
import { isGuestHome, readerFeeds, resolveFeed } from "../../web/src/lib/reader-feed";
import { parseSearchOptions, normalizeSearch, type SearchResponse } from "../../web/src/lib/search";
import type { FeedPage, FeedOptions, Topic, Source, Tag } from "../../web/src/lib/types";
import { ExtensionReaderTransport, publicOrigin } from "./transport";
import {
  destinationHref,
  linkDestination,
  navigate,
  readerRouteLocation,
  useNavigationState,
} from "./navigation";
import { LocalPage, PublicProfilePage } from "./pages";
import { catalogItem } from "./catalog";
import { Preview } from "./articles";
import { rememberArticles } from "./public-cache";
import { AccountSession, prepareSignOut, signedOut } from "./session";
import { PersonalFeed } from "../../web/src/components/personal-feed";
import { ReadLater } from "../../web/src/components/read-later";
import { TrendingContent } from "../../web/src/components/trending-content";
import { NotificationPreferencesProvider } from "../../web/src/components/notification-preferences-provider";
import { SignupNudge } from "../../web/src/components/signup-nudge";
import { ReaderPromptsProvider } from "../../web/src/components/reader-prompts";
import { ReaderContextProvider } from "../../web/src/components/reader-context";
import { ReaderNavigationRecovery } from "../../web/src/components/reader-navigation-recovery";
import { ReaderNotFound } from "../../web/src/components/reader-not-found";
import type { ReaderRoute } from "../../web/src/lib/reader-routes";
import { pageDestination, type ReaderPageDestination } from "../../web/src/lib/reader-destination";

configureReaderRuntime({
  request: new ExtensionReaderTransport(fetch).request,
  signedOut,
  prepareSignOut,
  reload: (href) => {
    window.history.replaceState(null, "", linkDestination(href));
    window.location.reload();
  },
  rememberArticles,
  navigate,
  destinationHref,
  publicOrigin,
  location: readerRouteLocation,
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
  match: Extract<ReaderRoute, { type: "reader" }>;
}) {
  const { user, loading: sessionLoading } = useUser();
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const refresh = () => setRevision((value) => value + 1);
    window.addEventListener("devfeed:extension-refresh", refresh);
    return () => window.removeEventListener("devfeed:extension-refresh", refresh);
  }, []);
  const url = new URL(route, publicOrigin);
  const routeDetail = match.detail;
  const queryParams = searchParamsFromUrl(url.searchParams);
  const { mode, filters } = resolveFeed(
    match.feed,
    queryParams,
    sessionLoading || Boolean(user),
    routeDetail?.contentType ?? match.contentType,
  );
  const detail =
    routeDetail ??
    (match.feed.mode === "latest" && !filters.topic && filters.source_id
      ? { kind: "sources" as const, slug: filters.source_id }
      : undefined);
  const feedDescriptor = detail ? readerFeeds.contextual : match.feed;
  const search = mode === "search";
  const key = search
    ? `${route}:${revision}`
    : `${route}:${revision}:${user?.user_id ?? "guest"}:${user?.csrf_token ?? ""}`;
  const trending = mode === "trending";
  const personal = mode === "personal";
  const bookmarks = mode === "bookmarks";
  const query = normalizeSearch(url.searchParams.get("q") ?? "");
  const searchOptions = parseSearchOptions(url.searchParams);
  const [state, setState] = useState<{
    key: string;
    feed?: FeedContentProps;
    search?: SearchResponse | null;
    trending?: PromiseSettledResult<FeedPage>;
    missing?: boolean;
  }>();
  useEffect(() => {
    if (sessionLoading || personal || bookmarks || state?.key === key) return;
    const controller = new AbortController();
    const { signal } = controller;
    async function load() {
      if (trending) {
        const [feed] = await Promise.allSettled([
          read<FeedPage>(`/api/v1/user/trending?${trendingFeedParams(filters.cursor)}`, signal),
        ]);
        if (!signal.aborted) setState({ key, trending: feed });
        return;
      }
      if (search) {
        const result = query
          ? await read<SearchResponse>(`/api/v1/search?${url.searchParams}`, signal).catch(
              () => null,
            )
          : null;
        if (!signal.aborted) setState({ key, search: result });
        return;
      }
      let item: Source | Topic | Tag | undefined;
      let resolvedFilters = filters;
      if (detail) {
        try {
          const kind = detail.kind;
          item = await catalogItem(kind, detail.slug, signal);
          if (!item) {
            if (!signal.aborted) setState({ key, missing: true });
            return;
          }
          resolvedFilters =
            kind === "tags"
              ? { ...filters, tag: item.slug }
              : kind === "topics"
                ? { ...filters, topic: item.slug }
                : { ...filters, source_id: item.id, source_slug: item.slug };
        } catch (reason) {
          if (!signal.aborted)
            setState({
              key,
              feed: {
                feedDescriptor,
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
        read<FeedOptions>(`/api/v1/feed/options?${params}`, signal),
      ]);
      if (!signal.aborted)
        setState({
          key,
          feed: {
            feedDescriptor,
            feed,
            options,
            filters: resolvedFilters,
            title: item?.name,
            topicId: detail?.kind === "topics" ? item?.id : undefined,
            tagId: detail?.kind === "tags" ? item?.id : undefined,
            description: item && "description" in item ? item.description : undefined,
            logoUrl: item && "logo_url" in item ? item.logo_url : undefined,
            logoVariants: item && "logo_variants" in item ? item.logo_variants : undefined,
            section: detail?.kind ?? "feed",
          },
        });
    }
    void load();
    return () => controller.abort();
    // Keep confirmed same-owner results mounted during a session recheck.
    // Public search pages retain loaded results across credential refreshes.
    // Other resources also include account identity in their data key.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, sessionLoading]);

  if (state?.key === key && state.missing) return <ReaderNotFound />;
  if (personal && !user && !(state?.key === key && state.feed))
    return (
      <UserShell section="personal">
        <LoadingSkeleton label="Loading your feed…" />
      </UserShell>
    );
  if ((personal && user) || bookmarks)
    return (
      <UserShell section={personal ? "personal" : "bookmarks"}>
        {personal ? (
          <PersonalFeed
            feedDescriptor={match.feed}
            filters={filters}
            key={user?.user_id}
            refreshKey={revision}
            cursor={filters.cursor || undefined}
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
  if (trending)
    return (
      <TrendingContent
        feed={state?.key === key ? state.trending : undefined}
        cursor={filters.cursor}
      />
    );
  const intro = !sessionLoading && isGuestHome(feedDescriptor, filters, Boolean(user));
  return (
    <UserShell filters={filters} section={detail?.kind ?? "feed"}>
      {intro && <GuestHomeIntro />}
      {state?.key === key && state.feed ? (
        <FeedContent key={key} {...state.feed} headingLevel={intro ? 2 : 1} withShell={false} />
      ) : (
        <LoadingSkeleton label="Loading articles…" />
      )}
    </UserShell>
  );
}

function ExtensionPage({ destination }: { destination: ReaderPageDestination }) {
  const route = destination.href;
  const match = destination.route;
  if (!match) return <ReaderNotFound />;
  switch (match.type) {
    case "profile":
      return <PublicProfilePage username={match.username} />;
    case "local":
      return <LocalPage key={route} route={route} match={match} />;
    case "reader":
      return (
        <>
          <Reader route={route} match={match} />
        </>
      );
  }
}

const directBackground = pageDestination("/latest");

function ExtensionReader() {
  const state = useNavigationState();
  const background =
    state.kind === "article" ? (state.background ?? directBackground) : state.destination;
  return (
    <>
      <ExtensionPage destination={background} />
      {state.kind === "article" && (
        <Preview slug={state.destination.slug} direct={state.background === null} />
      )}
    </>
  );
}

startExtensionAnalytics();

createRoot(document.getElementById("root")!).render(
  <AccountSession>
    <ReaderContextProvider>
      <ReaderPromptsProvider>
        <HeaderSponsorshipProvider>
          <ReaderNavigationRecovery />
          <ThemePreferencesProvider>
            <NotificationPreferencesProvider>
              <FeedPreferencesProvider>
                <ArticleNavigationProvider>
                  <SourceFollowsProvider>
                    <TagFollowsProvider>
                      <TopicFollowsProvider>
                        <ExtensionReader />
                        <SignupNudge />
                      </TopicFollowsProvider>
                    </TagFollowsProvider>
                  </SourceFollowsProvider>
                </ArticleNavigationProvider>
              </FeedPreferencesProvider>
            </NotificationPreferencesProvider>
          </ThemePreferencesProvider>
        </HeaderSponsorshipProvider>
      </ReaderPromptsProvider>
    </ReaderContextProvider>
  </AccountSession>,
);
