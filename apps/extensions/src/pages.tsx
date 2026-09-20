import { useEffect, useState, type ReactNode } from "react";
import { UserShell } from "../../web/src/components/user-shell";
import { LoadingSkeleton } from "../../web/src/components/loading-skeleton";
import { TopicsContent } from "../../web/src/components/topics-content";
import { SourcesContent } from "../../web/src/components/sources-content";
import { ProfileSettings } from "../../web/src/components/profile-settings";
import { AppearanceSettings } from "../../web/src/components/appearance-settings";
import { FeedSettings } from "../../web/src/components/feed-settings";
import { NotificationSettings } from "../../web/src/components/notification-settings";
import { TopicPreferences } from "../../web/src/components/topic-preferences";
import { SourcePreferences } from "../../web/src/components/source-preferences";
import { SourceSuggestion } from "../../web/src/components/source-suggestion";
import { catalogOffset } from "../../web/src/lib/catalog-page";
import type { Source, Topic } from "../../web/src/lib/types";
import { catalog } from "./catalog";
import { extensionRoute, type CatalogKind, type SettingsPage } from "./routes";

const settings: Record<SettingsPage, () => ReactNode> = {
  profile: () => <ProfileSettings />,
  appearance: () => <AppearanceSettings />,
  feed: () => <FeedSettings />,
  notifications: () => <NotificationSettings />,
  topics: () => <TopicPreferences />,
  sources: () => <SourcePreferences />,
};
export function LocalPage({ route }: { route: string }) {
  const url = new URL(route, "https://devfeed.tech");
  const match = extensionRoute(url.pathname);
  if (!match || match.type !== "local") return null;
  if (match.page === "settings") {
    const Component = settings[match.settings];
    return (
      <UserShell section="account">
        <Component />
      </UserShell>
    );
  }
  if (match.page === "source-suggestion")
    return (
      <UserShell section="sources">
        <SourceSuggestion />
      </UserShell>
    );
  return (
    <CatalogPage
      key={route}
      kind={match.catalog}
      offset={catalogOffset(url.searchParams.get("offset"))}
    />
  );
}
function CatalogPage({ kind, offset }: { kind: CatalogKind; offset: number }) {
  const [items, setItems] = useState<(Topic | Source)[]>();
  const [error, setError] = useState(false);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setError(false);
    void catalog(kind, AbortSignal.any([controller.signal, AbortSignal.timeout(30000)]), offset)
      .then((value) => {
        if (!controller.signal.aborted) setItems(value);
      })
      .catch(() => {
        if (!controller.signal.aborted) setError(true);
      });
    return () => controller.abort();
  }, [kind, offset, retry]);
  if (items)
    return kind === "topics" ? (
      <TopicsContent topics={items as Topic[]} offset={offset} />
    ) : (
      <SourcesContent sources={items as Source[]} offset={offset} />
    );
  return (
    <UserShell section={kind}>
      {error ? (
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
