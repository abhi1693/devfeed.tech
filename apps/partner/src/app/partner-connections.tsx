"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Pencil, Plus, RefreshCw, Filter } from "lucide-react";
import { ResourcePanel, ResourceHeading } from "@devfeed/ui/resource-layout";
import { formatDate, defaultDateTimePreferences } from "@devfeed/ui/date-format";
import { portalPath, accountSettingsPath } from "@/lib/routes";
import { Button } from "@devfeed/ui/button";
import { Input } from "@devfeed/ui/input";
import { Badge } from "@devfeed/ui/badge";
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "@devfeed/ui/table";
import { usePartnerRequest, isPartnerRequestCancelled } from "@/lib/partner-request";
import type { Account } from "@/lib/types";
import type {
  PartnerConnectionOut,
  WorkspaceConnectionOut,
  WorkspaceConnectionsOut,
  PartnerConnectionJobsOut,
} from "@/lib/api/generated/models";
import {
  PartnerConnectionGetResponse,
  PartnerWorkspaceConnectionsResponse,
  PartnerWorkspaceConnectionGetResponse,
  PartnerWorkspaceConnectionJobsResponse,
} from "@/lib/api/generated/validators";
import { PartnerConnectionForm, type ConnectionFormInput } from "./partner-connection-form";

export type ConnectionsRoute = {
  view: "list" | "create" | "detail" | "edit" | "disconnect";
  connectionId?: string;
  offset?: number;
  q?: string;
};

export function connectionPath(connectionId?: string, action?: "edit" | "disconnect") {
  return `/connections${connectionId ? `/${encodeURIComponent(connectionId)}` : ""}${action ? `/${action}` : ""}`;
}

function connectionListPath({
  offset = 0,
  q = "",
  accountId,
}: { offset?: number; q?: string; accountId?: string } = {}) {
  const params = new URLSearchParams();
  if (offset) params.set("connection_offset", String(offset));
  if (q) params.set("q", q);
  if (accountId) params.set("account_id", accountId);
  return `/connections${params.size ? `?${params}` : ""}`;
}

export function parseConnection(value: unknown, connectionId?: string) {
  const connection = PartnerWorkspaceConnectionGetResponse.parse(value);
  if (connectionId && connection.provider !== connectionId)
    throw new Error("Unexpected import connection.");
  return connection;
}

function parseConnectionWrite(value: unknown, accountId: string, connectionId: string) {
  const connection = PartnerConnectionGetResponse.parse(value);
  if (connection.account_id !== accountId || connection.provider !== connectionId)
    throw new Error("Unexpected import connection.");
  return { ...connection, account_id: accountId };
}

function ConnectionStatus({ connection }: { connection: PartnerConnectionOut }) {
  const state =
    connection.state === "disconnected"
      ? "Disconnected"
      : !connection.enabled
        ? "Paused"
        : connection.state === "idle"
          ? "Ready"
          : connection.state === "syncing"
            ? "Syncing"
            : "Needs attention";
  return (
    <Badge
      variant={state === "Ready" ? "success" : state === "Needs attention" ? "warning" : "neutral"}
    >
      {state}
    </Badge>
  );
}

function DateValue({ value, empty = "Not synced yet" }: { value: string | null; empty?: string }) {
  return value ? (
    <time dateTime={value}>{formatDate(value, defaultDateTimePreferences)}</time>
  ) : (
    <span className="text-muted-foreground">{empty}</span>
  );
}

const conflictMessage =
  "This connection changed since you opened it. Refresh the page before saving again.";

export function PartnerConnections({
  filterAccount,
  csrfToken,
  onUnavailable,
  route = { view: "list" },
}: {
  filterAccount?: Account;
  csrfToken: string;
  onUnavailable: () => void;
  route?: ConnectionsRoute;
}) {
  const router = useRouter();
  const request = usePartnerRequest({ onUnavailable });
  const [page, setPage] = useState<WorkspaceConnectionsOut>();
  const [connection, setConnection] = useState<WorkspaceConnectionOut>();
  const [jobs, setJobs] = useState<PartnerConnectionJobsOut>();
  const [jobsError, setJobsError] = useState("");
  const [credentialsAvailable, setCredentialsAvailable] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [refresh, setRefresh] = useState(0);
  const accountRequest = usePartnerRequest({ accountId: connection?.account_id, onUnavailable });
  const { view, connectionId } = route;
  const query = route.q ?? "";
  const [search, setSearch] = useState(query);
  const offset =
    Number.isSafeInteger(route.offset) && (route.offset ?? 0) >= 0 ? (route.offset ?? 0) : 0;
  const basePath = connectionPath();
  const detailPath = connectionId ? connectionPath(connectionId) : basePath;

  useEffect(() => {
    const controller = new AbortController();
    const common = { signal: controller.signal, unavailableOnNotFound: false };
    async function load() {
      setLoading(true);
      setError("");
      setJobsError("");
      setConnection(undefined);
      setPage(undefined);
      setJobs(undefined);
      try {
        if (view === "list" || view === "create") {
          const result = await request(
            `connections?limit=${view === "list" ? 25 : 1}&offset=${view === "list" ? offset : 0}&q=${encodeURIComponent(view === "list" ? query : "")}${view === "list" && filterAccount ? `&account_id=${encodeURIComponent(filterAccount.id)}` : ""}`,
            {
              ...common,
              errorMessage: "Could not load import connections. Please retry.",
              parse: (value) => {
                const result = PartnerWorkspaceConnectionsResponse.parse(value);
                if (
                  filterAccount &&
                  view === "list" &&
                  result.items.some((item) => item.account_id !== filterAccount.id)
                )
                  throw new Error("Unexpected account filter.");
                return result;
              },
            },
          );
          if (controller.signal.aborted) return;
          setPage(result);
          setCredentialsAvailable(result.credentials_available);
        }
        if (view !== "list" && view !== "create") {
          if (!connectionId) throw new Error("Import connection not found.");
          const result = await request(`connections/${encodeURIComponent(connectionId)}`, {
            ...common,
            errorMessage:
              "Could not load this import connection. It may no longer belong to this account.",
            parse: (value) => parseConnection(value, connectionId),
          });
          if (controller.signal.aborted) return;
          setConnection(result);
          setCredentialsAvailable(result.credentials_available);
        }
      } catch (reason) {
        if (!controller.signal.aborted && !isPartnerRequestCancelled(reason))
          setError(reason instanceof Error ? reason.message : "Could not load import connections.");
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }
    void load();
    return () => controller.abort();
  }, [filterAccount, connectionId, offset, query, refresh, request, view]);

  useEffect(() => {
    if (view !== "detail" || !connectionId) return;
    const controller = new AbortController();
    async function loadJobs() {
      try {
        const result = await request(
          `connections/${encodeURIComponent(connectionId!)}/jobs?limit=10&offset=0`,
          {
            signal: controller.signal,
            unavailableOnNotFound: false,
            errorMessage: "Could not load import runs. Refresh to try again.",
            parse: (value) => PartnerWorkspaceConnectionJobsResponse.parse(value),
          },
        );
        if (!controller.signal.aborted) {
          setJobs(result);
          setJobsError("");
        }
      } catch (reason) {
        if (!controller.signal.aborted && !isPartnerRequestCancelled(reason))
          setJobsError(reason instanceof Error ? reason.message : "Could not load import runs.");
      }
    }
    void loadJobs();
    return () => controller.abort();
  }, [connectionId, refresh, request, view]);

  async function save(input: ConnectionFormInput) {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const result = await (connection ? accountRequest : request)(
        connection ? `connections/${encodeURIComponent(connection.provider)}` : "connections",
        {
          method: connection ? "PUT" : "POST",
          headers: { "content-type": "application/json", "x-csrf-token": csrfToken },
          body: JSON.stringify({
            ...input,
            ...(connection ? { expected_revision: connection.revision } : {}),
          }),
          unavailableOnNotFound: false,
          statusMessages: {
            409: conflictMessage,
            422: "Check the API settings, mapping paths and authentication, then try again.",
          },
          errorMessage: "Could not save this import connection. Please retry.",
          invalidMessage:
            "Could not confirm the saved import connection. Refresh to check its status.",
          parse: (value) => {
            const saved = connection
              ? parseConnectionWrite(value, connection.account_id, connection.provider)
              : parseConnection(value);
            if (input.account_id && saved.account_id !== input.account_id)
              throw new Error("Unexpected saved account.");
            if (
              saved.name !== input.name ||
              saved.enabled !== input.enabled ||
              (input.sync_interval_minutes !== undefined &&
                saved.sync_interval_minutes !== input.sync_interval_minutes)
            ) {
              throw new Error("Unexpected saved settings.");
            }
            return saved;
          },
        },
      );
      router.push(connectionPath(result.provider));
    } catch (reason) {
      if (!isPartnerRequestCancelled(reason))
        setError(reason instanceof Error ? reason.message : "Could not save this connection.");
    } finally {
      setBusy(false);
    }
  }

  async function act(action: "sync" | "pause" | "resume" | "disconnect") {
    if (!connection || busy) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await accountRequest(
        `connections/${encodeURIComponent(connection.provider)}${action === "disconnect" ? "" : "/actions"}`,
        {
          method: action === "disconnect" ? "DELETE" : "POST",
          headers: { "content-type": "application/json", "x-csrf-token": csrfToken },
          body: JSON.stringify({
            ...(action === "disconnect" ? {} : { action }),
            expected_revision: connection.revision,
          }),
          unavailableOnNotFound: false,
          statusMessages: { 409: conflictMessage },
          errorMessage: "Could not update this import connection. Please retry.",
          invalidMessage: "Could not confirm this import connection. Refresh to check its status.",
          parse: (value) => parseConnectionWrite(value, connection.account_id, connection.provider),
        },
      );
      setConnection({ ...connection, ...result });
      if (action === "disconnect") router.push(connectionPath());
      else {
        setNotice(
          action === "sync"
            ? "Sync request accepted. Refresh import runs to follow its progress."
            : action === "pause"
              ? "Scheduled imports paused."
              : "Scheduled imports enabled.",
        );
        if (action === "sync") setRefresh((value) => value + 1);
      }
    } catch (reason) {
      if (!isPartnerRequestCancelled(reason))
        setError(reason instanceof Error ? reason.message : "Could not update this connection.");
    } finally {
      setBusy(false);
    }
  }

  const title =
    view === "create"
      ? "Create import connection"
      : view === "edit"
        ? `Edit ${connection?.name ?? "import connection"}`
        : view === "disconnect"
          ? `Disconnect ${connection?.name ?? "import connection"}`
          : view === "detail"
            ? (connection?.name ?? "Import connection")
            : "Import connections";
  return (
    <section className="space-y-6" aria-label="Import connections">
      <ResourceHeading
        homeHref={portalPath()}
        homeLabel="Dashboard"
        siteTitle="DevFeed Partners"
        title={title}
        description={
          view === "list" ? "Connect your product catalog and manage scheduled imports." : undefined
        }
        trail={
          view === "list"
            ? []
            : [
                { label: "Import connections", href: basePath },
                ...(connection && view !== "detail"
                  ? [{ label: connection.name, href: detailPath }]
                  : []),
              ]
        }
      >
        {view === "list" && (
          <Button asChild>
            <Link href={`${basePath}/new`} prefetch={false}>
              <Plus aria-hidden />
              Create connection
            </Link>
          </Button>
        )}
        {(view === "list" || view === "detail") && (
          <Button
            variant="outline"
            disabled={busy || loading}
            onClick={() => setRefresh((value) => value + 1)}
          >
            <RefreshCw aria-hidden />
            Refresh
          </Button>
        )}
        {view === "detail" && connection && (
          <Button asChild variant="outline">
            <Link href={connectionPath(connection.provider, "edit")} prefetch={false}>
              <Pencil aria-hidden />
              Edit
            </Link>
          </Button>
        )}
      </ResourceHeading>
      {view === "list" && (
        <div className="flex flex-wrap items-end gap-3">
          <form
            onSubmit={(event) => {
              event.preventDefault();
              router.push(connectionListPath({ q: search.trim(), accountId: filterAccount?.id }));
            }}
            className="flex min-w-0 flex-1 items-end gap-2"
          >
            <label className="grid min-w-0 max-w-md flex-1 gap-2 text-sm font-medium">
              Search connections
              <Input
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                maxLength={200}
                placeholder="Name, provider or account"
              />
            </label>
            <Button type="submit" variant="outline">
              Search
            </Button>
          </form>
          {filterAccount && (
            <div className="flex items-center gap-2 text-sm">
              <span>Account: {filterAccount.name}</span>
              <Button variant="ghost" size="sm" asChild>
                <Link href={connectionListPath({ q: query })} prefetch={false}>
                  Clear account filter
                </Link>
              </Button>
            </div>
          )}
        </div>
      )}
      {loading && (
        <p role="status" className="text-sm text-muted-foreground">
          Loading import connections…
        </p>
      )}
      {error && view !== "create" && (view !== "edit" || !connection) && (
        <div role="alert" className="notice">
          {error}
          <Button
            variant="outline"
            disabled={busy}
            onClick={() => setRefresh((value) => value + 1)}
          >
            Retry
          </Button>
        </div>
      )}
      {notice && (
        <p role="status" className="notice">
          {notice}
        </p>
      )}
      {!loading && view === "list" && page && (
        <>
          {page.items.length ? (
            <div className="overflow-x-auto rounded-md border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Name</TableHead>
                    <TableHead>Account</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead className="text-right">Products</TableHead>
                    <TableHead>Last synced</TableHead>
                    <TableHead>Next sync</TableHead>
                    <TableHead>
                      <span className="sr-only">Actions</span>
                    </TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {page.items.map((item) => (
                    <TableRow key={item.provider}>
                      <TableCell>
                        <Link
                          href={connectionPath(item.provider)}
                          prefetch={false}
                          className="font-medium text-primary hover:underline"
                        >
                          {item.name}
                        </Link>
                        <span className="mt-1 block text-xs text-muted-foreground">
                          {item.api_url}
                        </span>
                      </TableCell>
                      <TableCell>
                        <div className="flex items-center gap-1">
                          <Link
                            href={accountSettingsPath(item.account_id)}
                            className="text-primary hover:underline"
                            prefetch={false}
                          >
                            {item.account_name}
                          </Link>
                          <Button asChild variant="ghost" size="icon-xs">
                            <Link
                              href={connectionListPath({ q: query, accountId: item.account_id })}
                              aria-label={`Filter connections by ${item.account_name}`}
                              prefetch={false}
                            >
                              <Filter aria-hidden />
                            </Link>
                          </Button>
                        </div>
                      </TableCell>
                      <TableCell>
                        <ConnectionStatus connection={item} />
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {item.products.toLocaleString()}
                      </TableCell>
                      <TableCell>
                        <DateValue value={item.last_sync_at} />
                      </TableCell>
                      <TableCell>
                        <DateValue
                          value={item.next_sync_at}
                          empty={item.enabled ? "Waiting for schedule" : "Paused"}
                        />
                      </TableCell>
                      <TableCell>
                        <Link
                          href={connectionPath(item.provider, "edit")}
                          prefetch={false}
                          className="text-sm text-primary hover:underline"
                          aria-label={`Edit ${item.name}`}
                        >
                          Edit
                        </Link>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          ) : (
            <ResourcePanel
              title={query || filterAccount ? "No matching connections" : "No import connections"}
            >
              <p className="mb-4 text-sm text-muted-foreground">
                Connect an API to keep your catalog up to date. Imported products appear in Products
                for placement controls and previews.
              </p>
              <Button asChild>
                <Link href={query || filterAccount ? basePath : `${basePath}/new`} prefetch={false}>
                  {query || filterAccount ? "Clear filters" : "Create your first connection"}
                </Link>
              </Button>
            </ResourcePanel>
          )}
          {page.total > page.limit && (
            <nav
              aria-label="Import connection pages"
              className="flex flex-wrap items-center gap-3 text-sm"
            >
              <Button asChild variant="outline" disabled={offset === 0}>
                <Link
                  href={connectionListPath({
                    offset: Math.max(0, offset - page.limit),
                    q: query,
                    accountId: filterAccount?.id,
                  })}
                  prefetch={false}
                >
                  Previous
                </Link>
              </Button>
              <span>
                {page.items.length ? offset + 1 : 0}–{offset + page.items.length} of {page.total}
              </span>
              <Button asChild variant="outline" disabled={offset + page.limit >= page.total}>
                <Link
                  href={connectionListPath({
                    offset: offset + page.limit,
                    q: query,
                    accountId: filterAccount?.id,
                  })}
                  prefetch={false}
                >
                  Next
                </Link>
              </Button>
            </nav>
          )}
        </>
      )}
      {!loading && (view === "create" || (view === "edit" && connection)) && (
        <PartnerConnectionForm
          key={`${connection?.provider ?? "new"}:${connection?.revision ?? 0}`}
          connection={connection}
          credentialsAvailable={credentialsAvailable}
          cancelHref={detailPath}
          saving={busy}
          error={error}
          onSave={save}
          onUnavailable={onUnavailable}
        />
      )}
      {!loading && view === "disconnect" && connection && (
        <ResourcePanel title="Disconnect this import connection">
          <p className="mb-3 text-sm">
            Disconnect {connection.name} and remove it from your list. Your imported products and
            historical performance remain available; saved API credentials are removed.
          </p>
          <p className="mb-5 text-sm text-muted-foreground">
            To stop imports temporarily and keep this connection and its credential, choose Pause
            imports on the details page.
          </p>
          <div className="flex flex-wrap gap-3">
            <Button variant="destructive" loading={busy} onClick={() => void act("disconnect")}>
              Disconnect connection
            </Button>
            <Button asChild variant="outline" disabled={busy}>
              <Link href={detailPath} prefetch={false}>
                Cancel
              </Link>
            </Button>
          </div>
        </ResourcePanel>
      )}
      {!loading && view === "detail" && connection && (
        <>
          <div className="flex flex-wrap gap-3">
            <Button
              loading={busy}
              disabled={!connection.enabled || connection.state === "syncing"}
              onClick={() => void act("sync")}
            >
              <RefreshCw aria-hidden />
              Sync now
            </Button>
            {!connection.enabled &&
            connection.auth_mode !== "none" &&
            !connection.credential_configured &&
            !connection.credentials_managed ? (
              <Button asChild variant="outline">
                <Link href={connectionPath(connection.provider, "edit")} prefetch={false}>
                  Configure credential to reconnect
                </Link>
              </Button>
            ) : (
              <Button
                variant="outline"
                disabled={busy}
                onClick={() => void act(connection.enabled ? "pause" : "resume")}
              >
                {connection.enabled
                  ? "Pause imports"
                  : connection.state === "disconnected"
                    ? "Reconnect"
                    : "Resume imports"}
              </Button>
            )}
            {connection.state !== "disconnected" && (
              <Button asChild variant="destructive-ghost" disabled={busy}>
                <Link href={connectionPath(connection.provider, "disconnect")} prefetch={false}>
                  Disconnect
                </Link>
              </Button>
            )}
          </div>
          <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
            <div className="space-y-6">
              <ResourcePanel
                title="Import connection"
                fields={[
                  { label: "Name", value: connection.name },
                  {
                    label: "Partner account",
                    value: (
                      <Link
                        href={accountSettingsPath(connection.account_id)}
                        prefetch={false}
                        className="text-primary hover:underline"
                      >
                        {connection.account_name}
                      </Link>
                    ),
                  },
                  { label: "API", value: <span className="break-all">{connection.api_url}</span> },
                  {
                    label: "Sync interval",
                    value: `${connection.sync_interval_minutes.toLocaleString()} minutes`,
                  },
                  {
                    label: "Authentication",
                    value: connection.credentials_managed
                      ? "Managed by DevFeed"
                      : connection.credential_configured
                        ? connection.auth_mode === "bearer"
                          ? "Bearer token configured"
                          : "API key configured"
                        : "No credential configured",
                  },
                ]}
              />
              <ResourcePanel
                title="Imported products"
                fields={[
                  {
                    label: "Total",
                    value: (
                      <Link
                        href={`/products?account_id=${encodeURIComponent(connection.account_id)}`}
                        className="text-primary hover:underline"
                        prefetch={false}
                      >
                        {connection.products.toLocaleString()}
                      </Link>
                    ),
                  },
                  {
                    label: "Accepted",
                    value: (connection.commercial_approved ?? 0).toLocaleString(),
                  },
                  { label: "Qualified", value: connection.qualified.toLocaleString() },
                  { label: "Checking", value: connection.checking.toLocaleString() },
                  { label: "Needs attention", value: connection.needs_attention.toLocaleString() },
                  { label: "Excluded", value: connection.excluded.toLocaleString() },
                ]}
              />
            </div>
            <ResourcePanel
              title="Sync status"
              fields={[
                { label: "Status", value: <ConnectionStatus connection={connection} /> },
                { label: "Last synced", value: <DateValue value={connection.last_sync_at} /> },
                {
                  label: "Next sync",
                  value: <DateValue value={connection.next_sync_at} empty="Not scheduled" />,
                },
                ...(connection.error ? [{ label: "Last error", value: connection.error }] : []),
              ]}
            />
          </div>
          <ResourcePanel title="Recent import runs">
            {jobsError ? (
              <p role="alert" className="text-sm">
                {jobsError}
              </p>
            ) : !jobs ? (
              <p role="status" className="text-sm text-muted-foreground">
                Loading import runs…
              </p>
            ) : jobs.items.length ? (
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Operation</TableHead>
                      <TableHead>Status</TableHead>
                      <TableHead>Requested</TableHead>
                      <TableHead>Finished</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {jobs.items.map((job) => (
                      <TableRow key={job.id}>
                        <TableCell>
                          {job.operation === "sync_product"
                            ? "Product import"
                            : job.operation === "assess"
                              ? "Product assessment"
                              : "Catalog sync"}
                        </TableCell>
                        <TableCell>
                          <Badge
                            variant={
                              job.status === "succeeded"
                                ? "success"
                                : job.status === "failed"
                                  ? "warning"
                                  : "neutral"
                            }
                          >
                            {job.status}
                          </Badge>
                          {job.error && (
                            <span className="mt-1 block text-xs text-muted-foreground">
                              {job.error}
                            </span>
                          )}
                        </TableCell>
                        <TableCell>
                          <DateValue value={job.created_at} />
                        </TableCell>
                        <TableCell>
                          <DateValue
                            value={job.finished_at}
                            empty={
                              job.status === "queued"
                                ? "Queued"
                                : job.status === "running"
                                  ? "In progress"
                                  : "Not recorded"
                            }
                          />
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">
                No import runs yet. Enable this connection and choose Sync now to import your
                catalog.
              </p>
            )}
          </ResourcePanel>
        </>
      )}
    </section>
  );
}
