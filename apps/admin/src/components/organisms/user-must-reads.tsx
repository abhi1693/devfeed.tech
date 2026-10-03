"use client";

import Link from "next/link";
import { useCallback, useState } from "react";
import { DataTable, type DataTableColumn } from "@/components/molecules/data-table";
import { DateTime } from "@/components/molecules/date-time";
import { adminUserMustReads } from "@/lib/api/generated/admin";
import type { AdminUserDetail, AdminUserMustRead } from "@/lib/api/generated/models";
import { recordHref } from "@/lib/routes";
import { useRefreshInterval } from "@/lib/use-refresh-interval";
import { useRequest } from "@/lib/use-request";

const columns: DataTableColumn<AdminUserMustRead>[] = [
  { id: "position", accessorKey: "position", header: "Pick" },
  {
    id: "title",
    accessorKey: "title",
    header: "Article",
    cell: ({ row }) => (
      <Link
        className="text-blue-700 hover:underline dark:text-blue-400"
        href={recordHref("articles", row.original)}
      >
        {row.original.title}
      </Link>
    ),
  },
  { id: "reason", accessorKey: "reason", header: "Selected because" },
  {
    id: "read",
    header: "Reading status",
    cell: ({ row }) => (row.original.read ? "Read" : "Unread"),
  },
];

export function UserMustReads({ user }: { user: AdminUserDetail }) {
  const refresh = useRefreshInterval();
  const [revision, setRevision] = useState(0);
  const load = useCallback(
    (signal: AbortSignal) => adminUserMustReads(user.id, undefined, { signal }),
    [user.id],
  );
  const result = useRequest(
    `users/${user.id}/must-reads/${user.computed_at}/${user.feed_status}/${revision}`,
    load,
    refresh * 1000,
  );
  const selection = result.data;
  const items = selection?.items ?? [];
  const read = items.filter((item) => item.read).length;
  return (
    <section className="min-w-0 space-y-3" aria-label="Today’s Must Reads">
      <h3 className="font-semibold">Today’s Must Reads</h3>
      <p className="text-sm text-muted-foreground">
        The daily selection saved for this user, with recommendation reasons and reading progress.
      </p>
      {selection && (
        <dl className="flex flex-wrap gap-x-8 gap-y-3 text-sm">
          <div>
            <dt className="text-muted-foreground">Selection day</dt>
            <dd>
              {selection.selection_date} · {selection.timezone}
            </dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Reading progress</dt>
            <dd>{selection.generated ? `${read} of ${items.length} read` : "Not generated"}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Daily popup</dt>
            <dd>
              {!selection.generated ? (
                "Not generated"
              ) : selection.presented_at ? (
                <>
                  <span>Shown · </span>
                  <DateTime value={selection.presented_at} />
                </>
              ) : (
                "Not shown yet"
              )}
            </dd>
          </div>
        </dl>
      )}
      <DataTable
        label="Today’s Must Reads"
        data={items}
        columns={columns}
        getRowId={(row) => row.id}
        loading={result.loading}
        error={result.error}
        onRetry={() => setRevision((value) => value + 1)}
        empty={
          <span className="text-muted-foreground">
            {selection?.generated
              ? "No articles from today’s selection are currently available."
              : "No Must Reads have been generated for this user today."}
          </span>
        }
      />
    </section>
  );
}
