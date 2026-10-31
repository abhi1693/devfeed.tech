import { useEffect, useState, type ReactNode } from "react";
import { McpConsent } from "../../web/src/components/mcp-consent";
import { McpContent } from "../../web/src/components/mcp-content";
import { UserShell } from "../../web/src/components/user-shell";
import { LoadingSkeleton } from "../../web/src/components/loading-skeleton";
import { TagsContent } from "../../web/src/components/tags-content";
import { TopicsContent } from "../../web/src/components/topics-content";
import { SourcesContent } from "../../web/src/components/sources-content";
import { ProfileSettings } from "../../web/src/components/profile-settings";
import { Leaderboard } from "../../web/src/components/leaderboard";
import { DevCardCreator } from "../../web/src/components/dev-card-creator";
import { AppearanceSettings } from "../../web/src/components/appearance-settings";
import { FeedSettings } from "../../web/src/components/feed-settings";
import { NotificationSettings } from "../../web/src/components/notification-settings";
import { TopicPreferences } from "../../web/src/components/topic-preferences";
import { SourcePreferences } from "../../web/src/components/source-preferences";
import { SourceSuggestion } from "../../web/src/components/source-suggestion";
import { ReaderNotFound } from "../../web/src/components/reader-not-found";
import { catalogFilters } from "../../web/src/lib/catalog-page";
import type { CatalogPage } from "../../web/src/lib/catalog-page";
import type { Source, Topic, Tag } from "../../web/src/lib/types";
import type { UserProfile } from "../../web/src/lib/user";
import type { PublicReadingActivity } from "../../web/src/lib/server/public-dev-card";
import { PublicUserProfile } from "../../web/src/components/public-user-profile";
import { readerRequest } from "../../web/src/lib/reader-runtime";
import { catalog } from "./catalog";
import type { CatalogKind, ReaderRoute, SettingsPage } from "../../web/src/lib/reader-routes";

export function PublicProfilePage({ username }: { username: string }) {
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [activity, setActivity] = useState<PublicReadingActivity | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    setFailed(false);
    setProfile(null);
    void readerRequest(`/api/v1/users/${encodeURIComponent(username)}`, {
      signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]),
    })
      .then(async (response) => {
        if (!response.ok) throw new Error("Profile unavailable");
        const result = (await response.json()) as {
          profile: UserProfile;
          activity: PublicReadingActivity | null;
        };
        if (controller.signal.aborted) return;
        setProfile(result.profile);
        setActivity(result.activity);
      })
      .catch(() => {
        if (!controller.signal.aborted) setFailed(true);
      });
    return () => controller.abort();
  }, [username]);
  return (
    <UserShell>
      {profile ? (
        <PublicUserProfile profile={profile} activity={activity} />
      ) : failed ? (
        <section className="empty-state">
          <h1>Profile unavailable</h1>
          <p>This profile may be private or unavailable.</p>
        </section>
      ) : (
        <LoadingSkeleton label="Loading profile…" />
      )}
    </UserShell>
  );
}

const settings: Record<SettingsPage, () => ReactNode> = {
  profile: () => <ProfileSettings />,
  appearance: () => <AppearanceSettings />,
  feed: () => <FeedSettings />,
  notifications: () => <NotificationSettings />,
  topics: () => <TopicPreferences />,
  sources: () => <SourcePreferences />,
};
export function LocalPage({
  route,
  match,
}: {
  route: string;
  match: Extract<ReaderRoute, { type: "local" }>;
}) {
  const url = new URL(route, "https://devfeed.tech");
  switch (match.page) {
    case "dev-card":
      return (
        <UserShell>
          <DevCardCreator />
        </UserShell>
      );
    case "leaderboard":
      return (
        <UserShell section="leaderboard">
          <Leaderboard />
        </UserShell>
      );
    case "mcp-authorize":
      return (
        <UserShell section="mcp">
          <McpConsent requestId={url.searchParams.get("request") ?? ""} />
        </UserShell>
      );
    case "mcp":
      return (
        <UserShell section="mcp">
          <McpContent />
        </UserShell>
      );
    case "settings": {
      const Component = settings[match.settings];
      return (
        <UserShell section="account">
          <Component />
        </UserShell>
      );
    }
    case "source-suggestion":
      return (
        <UserShell section="sources">
          <SourceSuggestion />
        </UserShell>
      );
    case "catalog": {
      const filters = catalogFilters(url.searchParams, match.catalog);
      return <CatalogPage key={route} kind={match.catalog} {...filters} />;
    }
  }
}
function CatalogPage({
  kind,
  offset,
  query,
  letter,
}: {
  kind: CatalogKind;
  offset: number;
  query: string;
  letter: string;
}) {
  const [page, setPage] = useState<CatalogPage<Topic | Source | Tag>>();
  const [error, setError] = useState(false);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setError(false);
    setPage(undefined);
    void catalog(
      kind,
      AbortSignal.any([controller.signal, AbortSignal.timeout(30000)]),
      offset,
      query,
      letter,
    )
      .then((value) => {
        if (!controller.signal.aborted) setPage(value);
      })
      .catch(() => {
        if (!controller.signal.aborted) setError(true);
      });
    return () => controller.abort();
  }, [kind, offset, query, letter, retry]);
  if (page && offset > 0 && page.items.length === 0) return <ReaderNotFound />;
  return (
    <UserShell section={kind}>
      {page ? (
        kind === "tags" ? (
          <TagsContent
            tags={page.items as Tag[]}
            query={query}
            letter={letter}
            nextCursor={page.next_cursor}
            offset={offset}
          />
        ) : kind === "topics" ? (
          <TopicsContent
            topics={page.items as Topic[]}
            initialPage={page as CatalogPage<Topic>}
            offset={offset}
            query={query}
            withShell={false}
          />
        ) : (
          <SourcesContent
            sources={page.items as Source[]}
            initialPage={page as CatalogPage<Source>}
            offset={offset}
            query={query}
            withShell={false}
          />
        )
      ) : error ? (
        <section className="empty-state">
          <h1>Couldn’t load {kind}</h1>
          <button className="button" onClick={() => setRetry((value) => value + 1)}>
            Try again
          </button>
        </section>
      ) : (
        <LoadingSkeleton label={`Loading ${kind}…`} />
      )}
    </UserShell>
  );
}
