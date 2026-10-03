"use client";

import Link from "next/link";
import { useCallback, useState } from "react";
import { RefreshCw } from "lucide-react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/atoms/button";
import { DataTable, type DataTableColumn } from "@/components/molecules/data-table";
import { DateTime } from "@/components/molecules/date-time";
import { DataValue, InfoPanel } from "@/components/molecules/info-panel";
import { SearchField, searchScope } from "@/components/molecules/search-field";
import { StatusBadge } from "@/components/molecules/status-badge";
import type { AdminUserDetail } from "@/lib/api/generated/models";
import { listUserRecords, type RecordData } from "@/lib/resource-api";
import { humanize } from "@/lib/resources";
import { recordHref, type UserSection } from "@/lib/routes";
import { useRefreshInterval } from "@/lib/use-refresh-interval";
import { useRequest } from "@/lib/use-request";
import { useTableQuery } from "@/lib/use-table-query";
import { useAdmin } from "@/components/molecules/admin-session";
import { adminUserAnalysis } from "@/lib/api/generated/admin";
import { UserMustReads } from "./user-must-reads";
import { notify, notifyFailure } from "@/lib/notifications";

export function hasUserAnalysisData(user: AdminUserDetail) {
  return [
    user.followed_topics,
    user.followed_sources ?? 0,
    user.liked_articles,
    user.interests,
    user.recommendations,
  ].some((count) => (count ?? 0) > 0);
}

export function UserAnalysisAction({
  id,
  onQueued,
  disabled = false,
}: {
  id: string;
  onQueued: () => void;
  disabled?: boolean;
}) {
  const admin = useAdmin();
  const [busy, setBusy] = useState(false);
  async function analyze() {
    if (busy || disabled) return;
    setBusy(true);
    try {
      await adminUserAnalysis(id, { headers: { "X-CSRF-Token": admin.csrf_token } });
      notify.success("User analysis queued", {
        description: "Interests and recommendations will be rebuilt in the background.",
      });
      onQueued();
    } catch (error) {
      notifyFailure(error, "Could not queue user analysis");
    } finally {
      setBusy(false);
    }
  }
  return (
    <Button
      size="sm"
      variant="outline"
      disabled={disabled}
      title={disabled ? "No follows, likes, interests, or recommendations to analyze" : undefined}
      loading={busy}
      loadingText="Queuing analysis…"
      onClick={() => void analyze()}
    >
      <RefreshCw aria-hidden />
      Rerun analysis
    </Button>
  );
}

const labels: Record<UserSection, string> = {
  sources: "Followed sources",
  topics: "Followed topics",
  likes: "Liked articles",
  bookmarks: "Saved articles",
  reads: "Original article clicks",
  "reading-days": "Reading days",
  interests: "Topic interests",
  recommendations: "Prepared recommendations",
};
const defaultSort: Record<UserSection, string> = {
  sources: "-followed_at",
  topics: "-followed_at",
  likes: "-liked_at",
  bookmarks: "-bookmarked_at",
  reads: "-opened_at",
  "reading-days": "-read_date",
  interests: "-weight",
  recommendations: "position",
};
const reasons: Record<string, string> = {
  followed_source: "Followed source",
  followed_topic: "Followed topic",
  liked_topic: "Liked articles",
  related_topic: "Related topic",
};

export function UserActivitySummary({ user }: { user: AdminUserDetail }) {
  return (
    <InfoPanel title="Personalization">
      <div className="grid grid-cols-2 gap-3 pt-4">
        {(
          [
            ["topics", user.followed_topics],
            ["sources", user.followed_sources],
            ["likes", user.liked_articles],
            ["bookmarks", user.bookmarks],
            ["reads", user.reads],
            ["reading-days", user.reading_days],
            ["interests", user.interests],
            ["recommendations", user.recommendations],
          ] as const
        ).map(([section, count]) => (
          <Link
            key={section}
            className="rounded-md border p-3 hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring"
            href={recordHref("users", user, section)}
          >
            <span className="block text-lg font-semibold tabular-nums">{count ?? 0}</span>
            <span className="text-xs text-muted-foreground">{labels[section]}</span>
          </Link>
        ))}
      </div>
    </InfoPanel>
  );
}

function UserDisclosure({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <details className="min-w-0 rounded-lg border bg-card">
      <summary className="cursor-pointer rounded-lg px-6 py-4 text-sm font-semibold focus-visible:outline-2 focus-visible:outline-ring">
        {title}
      </summary>
      <div className="px-4 pb-4">{children}</div>
    </details>
  );
}

export function UserDetailsOverview({ user }: { user: AdminUserDetail }) {
  return (
    <div className="space-y-6">
      <div className="grid items-start gap-6 lg:grid-cols-2">
        <InfoPanel
          title="Account"
          fields={[
            { label: "Display name", value: user.name },
            { label: "Sign-in name", value: <DataValue value={user.sign_in_name} /> },
            { label: "Email", value: <DataValue value={user.email} /> },
            { label: "Joined", value: <DateTime value={user.created_at} /> },
            { label: "Last active", value: <DateTime value={user.last_seen_at} /> },
          ]}
        />
        <InfoPanel
          title="Reading activity"
          fields={[
            {
              label: "Last article click",
              value: user.last_read_at ? (
                <DateTime value={user.last_read_at} />
              ) : (
                "No recorded clicks"
              ),
            },
            { label: "Current streak", value: `${user.reading_streak.current_days} days` },
            { label: "Longest streak", value: `${user.reading_streak.longest_days} days` },
            { label: "Total reading days", value: user.reading_streak.total_days },
            {
              label: "Article clicks",
              value: (
                <Link
                  className="text-blue-700 hover:underline dark:text-blue-400"
                  href={recordHref("users", user, "reads")}
                >
                  {user.reads ?? 0} · View
                </Link>
              ),
            },
          ]}
        />
        <UserProfileDetails user={user} />
        <div className="min-w-0 space-y-6">
          <UserFeedStatus user={user} />
          <UserActivitySummary user={user} />
        </div>
      </div>
      <UserDisclosure title="Dev Card styling">
        <InfoPanel
          title="Dev Card"
          fields={[
            { label: "Theme", value: humanize(user.dev_card.theme ?? "classic") },
            { label: "Accent", value: humanize(user.dev_card.accent ?? "default") },
            { label: "Motion", value: humanize(user.dev_card.motion ?? "animated") },
            {
              label: "Technologies",
              value: <DataValue value={user.dev_card_technologies.map((item) => item.name)} />,
            },
            {
              label: "Featured stats",
              value: <DataValue value={(user.dev_card.stats ?? []).map(humanize)} />,
            },
          ]}
        />
      </UserDisclosure>
      <UserDisclosure title="Reader preferences">
        <div className="grid items-start gap-4 lg:grid-cols-2">
          <InfoPanel
            title="Feed preferences"
            fields={[
              { label: "View", value: <DataValue value={user.feed_preferences.view} /> },
              { label: "Languages", value: <DataValue value={user.feed_preferences.languages} /> },
              {
                label: "Content types",
                value: <DataValue value={user.feed_preferences.content_types} />,
              },
            ]}
          />
          <InfoPanel
            title="Appearance & notifications"
            fields={[
              { label: "Theme", value: <DataValue value={user.appearance_preferences.theme} /> },
              {
                label: "Timezone",
                value: <DataValue value={user.appearance_preferences.timezone} />,
              },
              {
                label: "Date format",
                value: <DataValue value={user.appearance_preferences.date_format} />,
              },
              {
                label: "Time format",
                value: <DataValue value={user.appearance_preferences.time_format} />,
              },
              {
                label: "Notification badge",
                value: <DataValue value={user.notification_preferences.show_badge} />,
              },
              {
                label: "Notification sound",
                value: <DataValue value={user.notification_preferences.sound} />,
              },
            ]}
          />
        </div>
      </UserDisclosure>
      <UserDisclosure title="Technical identifiers">
        <InfoPanel
          title="Record information"
          fields={[{ label: "User ID", value: <DataValue value={user.id} /> }]}
        />
      </UserDisclosure>
    </div>
  );
}

export function UserProfileDetails({ user }: { user: AdminUserDetail }) {
  return (
    <InfoPanel
      title="Reader profile"
      fields={[
        { label: "Username", value: <DataValue value={user.username} /> },
        { label: "Public profile", value: user.profile_public ? "Visible" : "Hidden" },
        ...(
          [
            ["Bio", user.profile_bio],
            ["Location", user.profile_location],
            ["About", user.profile_about],
          ] as const
        )
          .filter(([, value]) => !!value)
          .map(([label, value]) => ({ label, value: <DataValue value={value} /> })),
        ...(user.profile_links.length
          ? [
              {
                label: "Links",
                value: (
                  <ul className="space-y-2">
                    {user.profile_links.map((link) => (
                      <li key={link.url}>
                        <a
                          className="break-all text-blue-700 hover:underline dark:text-blue-400"
                          href={link.url}
                          target="_blank"
                          rel="noopener noreferrer"
                        >
                          {link.label || link.url}
                        </a>
                      </li>
                    ))}
                  </ul>
                ),
              },
            ]
          : []),
        ...(user.stack.length
          ? [
              {
                label: "Technology stack",
                value: (
                  <ul className="space-y-2">
                    {user.stack.map((item) => (
                      <li key={item.id}>
                        <Link
                          className="text-blue-700 hover:underline dark:text-blue-400"
                          href={recordHref("topics", item)}
                        >
                          {item.name}
                        </Link>
                        <span className="text-muted-foreground">
                          {" "}
                          · {humanize(item.section)}
                          {item.since_year ? ` · Since ${item.since_year}` : ""}
                        </span>
                      </li>
                    ))}
                  </ul>
                ),
              },
            ]
          : []),
      ]}
    />
  );
}

export function UserFeedStatus({ user }: { user: AdminUserDetail }) {
  if (!hasUserAnalysisData(user))
    return (
      <InfoPanel
        title="Recommendation refresh"
        fields={[
          { label: "Analysis", value: "Not needed" },
          {
            label: "Next refresh",
            value: "After the user follows a topic or source, or likes an article.",
          },
        ]}
      />
    );
  return (
    <InfoPanel
      title="Recommendation refresh"
      fields={[
        { label: "Feed status", value: <StatusBadge value={user.feed_status} /> },
        {
          label: "Last computed",
          value: user.computed_at ? <DateTime value={user.computed_at} /> : "Not computed yet",
        },
        {
          label: "Next refresh",
          value: user.next_refresh_at ? (
            <DateTime value={user.next_refresh_at} />
          ) : (
            "Waiting to be scheduled"
          ),
        },
        { label: "Expires", value: user.expires_at ? <DateTime value={user.expires_at} /> : "—" },
        { label: "Refresh failures", value: user.refresh_attempts },
      ]}
    />
  );
}

export function UserAnalysis({ user }: { user: AdminUserDetail }) {
  return (
    <div className="space-y-6">
      <div>
        <h2 className="font-semibold">User analysis</h2>
        <p className="mt-2 text-sm text-muted-foreground">
          {!hasUserAnalysisData(user)
            ? "Analysis is skipped because this user has no follows, likes, interests, or prepared recommendations."
            : !user.computed_at
              ? "Analysis has not completed yet. Rerun analysis to prepare this user’s interests and recommendations."
              : user.feed_status === "ready"
                ? "Latest stored interests and recommendations, based on followed topics, followed sources, liked articles, and related topics."
                : "Previous analysis results are shown below. They are awaiting a refresh and do not represent a ready personalized feed."}
        </p>
      </div>
      <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
        <UserActivitySummary user={user} />
        <UserFeedStatus user={user} />
      </div>
      <UserInterestPreview key={`${user.id}/interests`} user={user} />
      <UserMustReads key={`${user.id}/must-reads`} user={user} />
    </div>
  );
}

function UserInterestPreview({ user }: { user: AdminUserDetail }) {
  const section = "interests";
  const refresh = useRefreshInterval();
  const [revision, setRevision] = useState(0);
  const load = useCallback(
    (signal: AbortSignal) =>
      listUserRecords(
        user.id,
        section,
        {
          limit: 5,
          offset: 0,
          sort: defaultSort[section],
        },
        signal,
      ),
    [user.id],
  );
  const result = useRequest(
    `users/${user.id}/analysis/${section}/${user.computed_at}/${user.feed_status}/${revision}`,
    load,
    refresh * 1000,
  );
  const title = "Strongest topic interests";
  return (
    <section className="min-w-0 space-y-3" aria-label={title}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h3 className="font-semibold">{title}</h3>
        <Button variant="outline" size="sm" asChild>
          <Link href={recordHref("users", user, section)}>View all {section}</Link>
        </Button>
      </div>
      <p className="text-sm text-muted-foreground">
        The five highest-weight interests and the topics behind them. Weights are ranking signals,
        not confidence percentages.
      </p>
      <DataTable
        label={title}
        data={result.data?.items ?? []}
        columns={columns(section).map((column) => ({ ...column, enableSorting: false }))}
        getRowId={(row) => row.id}
        loading={result.loading}
        error={result.error}
        onRetry={() => setRevision((value) => value + 1)}
        empty={
          <span className="text-muted-foreground">
            No topic interests were stored. Source follows can still produce recommendations.
          </span>
        }
      />
    </section>
  );
}

function topicLink(id: unknown, name: unknown) {
  return name ? (
    <Link
      prefetch={false}
      className="break-words text-blue-700 hover:underline dark:text-blue-400"
      href={recordHref("topics", { id: String(id) })}
    >
      {String(name)}
    </Link>
  ) : (
    <span className="text-muted-foreground">Topic no longer available</span>
  );
}

function columns(section: UserSection): DataTableColumn<RecordData>[] {
  if (section === "reading-days")
    return [
      {
        id: "read_date",
        accessorKey: "read_date",
        header: "UTC day",
        enableSorting: true,
      },
      {
        id: "article_count",
        accessorKey: "article_count",
        header: "Articles clicked",
        enableSorting: true,
      },
      {
        id: "last_read_at",
        accessorKey: "last_read_at",
        header: "Last click",
        cell: ({ row }) => <DateTime value={String(row.original.last_read_at)} />,
      },
    ];
  const topics = section === "topics" || section === "interests";
  const catalog = topics || section === "sources";
  return [
    ...(section === "recommendations"
      ? [{ id: "position", accessorKey: "position", header: "Rank", enableSorting: true }]
      : []),
    {
      id: catalog ? "name" : "title",
      accessorKey: catalog ? "name" : "title",
      header: catalog ? (topics ? "Topic" : "Source") : "Article",
      enableSorting: true,
      cell: ({ row }) =>
        section === "sources" ? (
          <Link href={recordHref("sources", row.original)}>{String(row.original.name)}</Link>
        ) : topics ? (
          topicLink(row.original.id, row.original.name)
        ) : section === "reads" && !row.original.title ? (
          <span className="text-muted-foreground">Article no longer available</span>
        ) : (
          <Link
            prefetch={false}
            className="block max-w-lg break-words font-medium text-blue-700 hover:underline dark:text-blue-400"
            href={recordHref("articles", {
              id: String(row.original.article_id ?? row.original.id),
            })}
          >
            {String(row.original.title)}
          </Link>
        ),
    },
    {
      id: catalog ? "status" : "publication_status",
      header: catalog ? "Status" : "Publication",
      enableSorting: false,
      cell: ({ row }) =>
        row.original[catalog ? "status" : "publication_status"] ? (
          <StatusBadge value={row.original[catalog ? "status" : "publication_status"]} />
        ) : (
          "—"
        ),
    },
    ...(section === "topics" ||
    section === "sources" ||
    section === "likes" ||
    section === "bookmarks" ||
    section === "reads"
      ? [
          {
            id:
              section === "likes"
                ? "liked_at"
                : section === "bookmarks"
                  ? "bookmarked_at"
                  : section === "reads"
                    ? "opened_at"
                    : "followed_at",
            header:
              section === "likes"
                ? "Liked"
                : section === "bookmarks"
                  ? "Saved"
                  : section === "reads"
                    ? "Clicked"
                    : "Followed",
            enableSorting: true,
            cell: ({ row }: { row: { original: RecordData } }) => (
              <DateTime
                value={String(
                  row.original[
                    section === "likes"
                      ? "liked_at"
                      : section === "bookmarks"
                        ? "bookmarked_at"
                        : section === "reads"
                          ? "opened_at"
                          : "followed_at"
                  ],
                )}
              />
            ),
          },
        ]
      : [
          {
            id: "reason",
            header: "Reason",
            enableSorting: section === "interests",
            cell: ({ row }: { row: { original: RecordData } }) =>
              reasons[String(row.original.reason)] ?? humanize(String(row.original.reason)),
          },
          {
            id: "seed_topic",
            header: "Based on",
            enableSorting: false,
            cell: ({ row }: { row: { original: RecordData } }) =>
              row.original.source_id ? (
                <Link href={recordHref("sources", { id: String(row.original.source_id) })}>
                  {String(row.original.source_name ?? "Source")}
                </Link>
              ) : (
                topicLink(row.original.seed_topic_id, row.original.seed_topic_name)
              ),
          },
          {
            id: section === "interests" ? "weight" : "score",
            header: section === "interests" ? "Weight" : "Score",
            enableSorting: true,
            cell: ({ row }: { row: { original: RecordData } }) =>
              Number(row.original[section === "interests" ? "weight" : "score"]).toLocaleString(
                undefined,
                { maximumFractionDigits: 1 },
              ),
          },
        ]),
    ...(section === "recommendations"
      ? [
          {
            id: "matching_topic",
            header: "Matching topic",
            enableSorting: false,
            cell: ({ row }: { row: { original: RecordData } }) =>
              row.original.source_id ? (
                <span className="text-muted-foreground">All source articles</span>
              ) : (
                topicLink(row.original.topic_id, row.original.topic_name)
              ),
          },
        ]
      : []),
  ];
}

export function UserRecords({ user, section }: { user: AdminUserDetail; section: UserSection }) {
  const query = useTableQuery(`users-${section}`).toString();
  return (
    <UserRecordsTable key={`${user.id}/${section}`} user={user} section={section} query={query} />
  );
}

function UserRecordsTable({
  user,
  section,
  query,
}: {
  user: AdminUserDetail;
  section: UserSection;
  query: string;
}) {
  const router = useRouter();
  const search = new URLSearchParams(query);
  const refresh = useRefreshInterval();
  const [revision, setRevision] = useState(0);
  const load = useCallback(
    (signal: AbortSignal) => {
      const params = Object.fromEntries(new URLSearchParams(query));
      return listUserRecords(
        user.id,
        section,
        {
          ...params,
          limit: Number(params.limit || 25),
          offset: Number(params.offset || 0),
          sort: params.sort || defaultSort[section],
        },
        signal,
      );
    },
    [user.id, section, query],
  );
  const result = useRequest(
    `users/${user.id}/${section}?${query}/${revision}`,
    load,
    refresh * 1000,
  );
  function change(values: Record<string, string>, replace = false) {
    const params = new URLSearchParams(query);
    for (const [key, value] of Object.entries(values)) {
      if (value) params.set(key, value);
      else params.delete(key);
    }
    router[replace ? "replace" : "push"](`${recordHref("users", user, section)}?${params}`, {
      scroll: false,
    });
  }
  return (
    <section className="min-w-0 space-y-4">
      <h2 className="font-semibold">{labels[section]}</h2>
      {(section === "interests" || section === "recommendations") && (
        <p className="text-sm text-muted-foreground">
          {user.feed_status === "ready"
            ? "Stored results from the last computation. Current publication and topic checks still apply when serving the user’s feed."
            : "These are stored results from the previous computation. They are not being served while the user’s feed is waiting for a refresh."}
        </p>
      )}
      <DataTable
        label={labels[section]}
        preferenceKey={`users-${section}`}
        columnChoices
        data={result.data?.items ?? []}
        columns={columns(section)}
        getRowId={(row) => row.id}
        loading={result.loading}
        error={result.error}
        onRetry={() => setRevision((value) => value + 1)}
        sort={search.get("sort") || defaultSort[section]}
        onSortChange={(sort) => change({ sort: sort || defaultSort[section], offset: "0" })}
        pagination={{
          total: result.data?.total ?? 0,
          limit: result.data?.limit ?? Number(search.get("limit") || 25),
          offset: result.data?.offset ?? Number(search.get("offset") || 0),
          onChange: change,
        }}
        toolbar={
          <div className="flex flex-wrap items-end gap-3">
            <SearchField
              className="max-w-lg flex-1"
              label={`Search ${labels[section].toLowerCase()}`}
              value={search.get("q") ?? ""}
              scopeKey={searchScope(query)}
              onSearch={(q) => change({ q, offset: "0" }, true)}
            />
            {search.get("q") && (
              <Button variant="ghost" onClick={() => change({ q: "", offset: "0" })}>
                Clear search
              </Button>
            )}
          </div>
        }
        empty={
          <span className="text-muted-foreground">
            No {labels[section].toLowerCase()} match these filters.
          </span>
        }
      />
    </section>
  );
}
