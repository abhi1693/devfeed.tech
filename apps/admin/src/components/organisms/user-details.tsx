"use client";

import Link from "next/link";
import { useCallback, useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/atoms/button";
import { DataTable, type DataTableColumn } from "@/components/molecules/data-table";
import { DateTime } from "@/components/molecules/date-time";
import { InfoPanel } from "@/components/molecules/info-panel";
import { SearchField, searchScope } from "@/components/molecules/search-field";
import { StatusBadge } from "@/components/molecules/status-badge";
import type { AdminUserDetail } from "@/lib/api/generated/models";
import { listUserRecords, type RecordData } from "@/lib/resource-api";
import { humanize } from "@/lib/resources";
import { recordHref, type UserSection } from "@/lib/routes";
import { useRefreshInterval } from "@/lib/use-refresh-interval";
import { useRequest } from "@/lib/use-request";
import { useTableQuery } from "@/lib/use-table-query";

const labels: Record<UserSection, string> = {
  topics: "Followed topics", likes: "Liked articles", interests: "Topic interests", recommendations: "Prepared recommendations",
};
const defaultSort: Record<UserSection, string> = {
  topics: "-followed_at", likes: "-liked_at", interests: "-weight", recommendations: "position",
};
const reasons: Record<string, string> = { followed_topic: "Followed topic", liked_topic: "Liked articles", related_topic: "Related topic" };

export function UserActivitySummary({ user }: { user: AdminUserDetail }) {
  return <InfoPanel title="Personalization" fields={([
    ["topics", user.followed_topics], ["likes", user.liked_articles],
    ["interests", user.interests], ["recommendations", user.recommendations],
  ] as const).map(([section, count]) => ({ label: labels[section], value: <Link className="text-blue-700 hover:underline dark:text-blue-400" href={recordHref("users", user, section)}>{count} · View</Link> }))} />;
}

export function UserFeedStatus({ user }: { user: AdminUserDetail }) {
  return <InfoPanel title="Recommendation refresh" fields={[
    { label: "Feed status", value: <StatusBadge value={user.feed_status} /> },
    { label: "Last computed", value: user.computed_at ? <DateTime value={user.computed_at} /> : "Not computed yet" },
    { label: "Next refresh", value: user.next_refresh_at ? <DateTime value={user.next_refresh_at} /> : "Waiting to be scheduled" },
    { label: "Expires", value: user.expires_at ? <DateTime value={user.expires_at} /> : "—" },
    { label: "Refresh failures", value: user.refresh_attempts },
  ]} />;
}

function topicLink(id: unknown, name: unknown) {
  return name ? <Link prefetch={false} className="break-words text-blue-700 hover:underline dark:text-blue-400" href={recordHref("topics", { id: String(id) })}>{String(name)}</Link> : <span className="text-muted-foreground">Topic no longer available</span>;
}

function columns(section: UserSection): DataTableColumn<RecordData>[] {
  const topics = section === "topics" || section === "interests";
  return [
    ...(section === "recommendations" ? [{ id: "position", accessorKey: "position", header: "Rank", enableSorting: true }] : []),
    { id: topics ? "name" : "title", accessorKey: topics ? "name" : "title", header: topics ? "Topic" : "Article", enableSorting: true,
      cell: ({ row }) => topics ? topicLink(row.original.id, row.original.name) : <Link prefetch={false} className="block max-w-lg break-words font-medium text-blue-700 hover:underline dark:text-blue-400" href={recordHref("articles", row.original)}>{String(row.original.title)}</Link> },
    { id: topics ? "status" : "publication_status", header: topics ? "Status" : "Publication", enableSorting: false,
      cell: ({ row }) => <StatusBadge value={row.original[topics ? "status" : "publication_status"]} /> },
    ...(section === "topics" || section === "likes" ? [{ id: section === "topics" ? "followed_at" : "liked_at", header: section === "topics" ? "Followed" : "Liked", enableSorting: true,
      cell: ({ row }: { row: { original: RecordData } }) => <DateTime value={String(row.original[section === "topics" ? "followed_at" : "liked_at"])} /> }] : [
      { id: "reason", header: "Reason", enableSorting: section === "interests", cell: ({ row }: { row: { original: RecordData } }) => reasons[String(row.original.reason)] ?? humanize(String(row.original.reason)) },
      { id: "seed_topic", header: "Based on", enableSorting: false, cell: ({ row }: { row: { original: RecordData } }) => topicLink(row.original.seed_topic_id, row.original.seed_topic_name) },
      { id: section === "interests" ? "weight" : "score", header: section === "interests" ? "Weight" : "Score", enableSorting: true,
        cell: ({ row }: { row: { original: RecordData } }) => Number(row.original[section === "interests" ? "weight" : "score"]).toLocaleString(undefined, { maximumFractionDigits: 1 }) },
    ]),
    ...(section === "recommendations" ? [{ id: "matching_topic", header: "Matching topic", enableSorting: false,
      cell: ({ row }: { row: { original: RecordData } }) => topicLink(row.original.topic_id, row.original.topic_name) }] : []),
  ];
}

export function UserRecords({ user, section }: { user: AdminUserDetail; section: UserSection }) {
  const query = useTableQuery(`users-${section}`).toString();
  return <UserRecordsTable key={`${user.id}/${section}`} user={user} section={section} query={query} />;
}

function UserRecordsTable({ user, section, query }: { user: AdminUserDetail; section: UserSection; query: string }) {
  const router = useRouter();
  const search = new URLSearchParams(query);
  const refresh = useRefreshInterval();
  const [revision, setRevision] = useState(0);
  const load = useCallback((signal: AbortSignal) => {
    const params = Object.fromEntries(new URLSearchParams(query));
    return listUserRecords(user.id, section, { ...params, limit: Number(params.limit || 25), offset: Number(params.offset || 0), sort: params.sort || defaultSort[section] }, signal);
  }, [user.id, section, query]);
  const result = useRequest(`users/${user.id}/${section}?${query}/${revision}`, load, refresh * 1000);
  function change(values: Record<string, string>, replace = false) {
    const params = new URLSearchParams(query);
    for (const [key, value] of Object.entries(values)) { if (value) params.set(key, value); else params.delete(key); }
    router[replace ? "replace" : "push"](`${recordHref("users", user, section)}?${params}`, { scroll: false });
  }
  return <section className="min-w-0 space-y-4">
    <h2 className="font-semibold">{labels[section]}</h2>
    {(section === "interests" || section === "recommendations") && <p className="text-sm text-muted-foreground">{user.feed_status === "ready" ? "Stored results from the last computation. Current publication and topic checks still apply when serving the user’s feed." : "These are stored results from the previous computation. They are not being served while the user’s feed is waiting for a refresh."}</p>}
    <DataTable label={labels[section]} preferenceKey={`users-${section}`} columnChoices
      data={result.data?.items ?? []} columns={columns(section)} getRowId={row => row.id}
      loading={result.loading} error={result.error} onRetry={() => setRevision(value => value + 1)}
      sort={search.get("sort") || defaultSort[section]} onSortChange={sort => change({ sort: sort || defaultSort[section], offset: "0" })}
      pagination={{ total: result.data?.total ?? 0, limit: result.data?.limit ?? Number(search.get("limit") || 25), offset: result.data?.offset ?? Number(search.get("offset") || 0), onChange: change }}
      toolbar={<div className="flex flex-wrap items-end gap-3"><SearchField className="max-w-lg flex-1" label={`Search ${labels[section].toLowerCase()}`} value={search.get("q") ?? ""} scopeKey={searchScope(query)} onSearch={q => change({ q, offset: "0" }, true)} />{search.get("q") && <Button variant="ghost" onClick={() => change({ q: "", offset: "0" })}>Clear search</Button>}</div>}
      empty={<span className="text-muted-foreground">No {labels[section].toLowerCase()} match these filters.</span>} />
  </section>;
}
