"use client";

import { partnerSyncInterval } from "@/lib/partner-interval";

import { useRefreshInterval } from "@/lib/use-refresh-interval";

import Link from "next/link";
import { Pencil, RefreshCw } from "lucide-react";
import { InfoPanel, DataValue } from "@/components/molecules/info-panel";
import { DateTime } from "@/components/molecules/date-time";
import { StatusBadge } from "@/components/molecules/status-badge";
import { PartnerRelated } from "./partner-related";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Button } from "@/components/atoms/button";
import { PageHeading } from "@/components/molecules/page-heading";
import { RequestState } from "@/components/molecules/request-state";
import { useAdmin } from "@/components/molecules/admin-session";
import { PartnerConnectionForm } from "./partner-connection-form";
import { partnerHref, partnersPath, partnershipTrail } from "./partner-connections";
import {
  dashboardV1AdminPartnerAccountsAccountIdDashboardGet,
  adminPartnerConnectionsList,
  adminPartnerProvidersList,
  adminPartnerConnectionAction,
} from "@/lib/api/generated/admin";
import type { ConnectionOut, PartnerProviderOut } from "@/lib/api/generated/models";

export function PartnerConnectionPage({
  provider,
  editing = false,
  section = "details",
}: {
  provider?: string;
  editing?: boolean;
  section?: "details" | "related";
}) {
  const refreshSeconds = useRefreshInterval();
  const router = useRouter();
  const admin = useAdmin();
  const [accountName, setAccountName] = useState<string>();
  const [connection, setConnection] = useState<ConnectionOut>();
  const [providers, setProviders] = useState<PartnerProviderOut[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error>();
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    let live = true;
    async function load() {
      try {
        const [connections, supported] = await Promise.all([
          adminPartnerConnectionsList(),
          adminPartnerProvidersList(),
        ]);
        const found = connections.find((c) => c.provider === provider);
        if (provider && !found) throw new Error("Partner not found.");
        const account =
          found?.account_id && !editing && section === "details"
            ? await dashboardV1AdminPartnerAccountsAccountIdDashboardGet(found.account_id, {
                limit: 1,
              })
            : undefined;
        if (live) {
          setConnection(found);
          setAccountName(account?.account.name);
          setProviders(
            provider
              ? supported
              : supported.filter((p) => !connections.some((c) => c.provider === p.provider)),
          );
          setError(undefined);
        }
      } catch (error) {
        if (live) setError(error instanceof Error ? error : new Error("Could not load partner"));
      } finally {
        if (live) setLoading(false);
      }
    }
    void load();
    // Keep the edit snapshot stable so a concurrent settings change returns a conflict.
    const timer =
      provider && !editing && refreshSeconds > 0
        ? setInterval(() => void load(), refreshSeconds * 1000)
        : undefined;
    return () => {
      live = false;
      clearInterval(timer);
    };
  }, [provider, editing, section, refresh, refreshSeconds]);
  const trail = [
    ...partnershipTrail,
    { label: "Partners", href: partnersPath },
    ...(editing && connection
      ? [{ label: connection.name, href: partnerHref(connection.provider) }]
      : []),
  ];
  async function sync() {
    if (!connection) return;
    setBusy(true);
    setError(undefined);
    try {
      setConnection(
        await adminPartnerConnectionAction(
          connection.provider,
          { action: "sync" },
          { headers: { "X-CSRF-Token": admin.csrf_token } },
        ),
      );
    } catch (error) {
      setError(error instanceof Error ? error : new Error("Could not sync partner"));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className={editing || !provider ? "max-w-4xl space-y-6" : "space-y-6"}>
      <PageHeading
        title={
          !provider
            ? "Create partner"
            : editing
              ? `Edit ${connection?.name ?? "partner"}`
              : (connection?.name ?? "Partner")
        }
        trail={trail}
      >
        {connection && !editing && (
          <>
            <Button
              variant="outline"
              size="sm"
              loading={busy}
              disabled={!connection.enabled || connection.state === "syncing"}
              onClick={() => void sync()}
            >
              <RefreshCw aria-hidden />
              Sync now
            </Button>
            <Button asChild variant="outline" size="sm">
              <Link href={`${partnerHref(connection.provider)}/edit`} prefetch={false}>
                <Pencil aria-hidden />
                Edit
              </Link>
            </Button>
          </>
        )}
      </PageHeading>
      <RequestState loading={loading} error={error} retry={() => setRefresh((n) => n + 1)} />
      {!loading &&
        !error &&
        (!provider || editing) &&
        (providers.length ? (
          <PartnerConnectionForm
            providers={providers}
            connection={connection}
            onSaved={(saved) => router.push(partnerHref(saved.provider))}
            onCancel={() =>
              router.push(connection ? partnerHref(connection.provider) : partnersPath)
            }
          />
        ) : (
          <p className="rounded-lg border bg-card p-6 text-sm text-muted-foreground">
            All supported partners have already been added. Edit an existing partner to change its
            settings.
          </p>
        ))}
      {connection && !editing && (
        <>
          <nav aria-label="Object sections" className="flex gap-5 overflow-x-auto border-b">
            {(["details", "related"] as const).map((tab) => (
              <Link
                key={tab}
                prefetch={false}
                href={partnerHref(connection.provider) + (tab === "details" ? "" : "/related")}
                aria-current={section === tab ? "page" : undefined}
                className={`whitespace-nowrap border-b-2 px-1 pb-3 text-sm ${section === tab ? "border-primary font-medium" : "border-transparent text-muted-foreground hover:text-foreground"}`}
              >
                {tab === "details" ? "Details" : "Related objects"}
              </Link>
            ))}
          </nav>
          {section === "related" ? (
            <PartnerRelated provider={connection.provider} />
          ) : (
            <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
              <div className="space-y-6">
                <InfoPanel
                  title="Partner"
                  fields={[
                    { label: "Name", value: <DataValue value={connection.name} /> },
                    { label: "Type", value: "Launch platform" },
                    {
                      label: "Account",
                      value: connection.account_id ? (
                        <Link
                          href={`/partnerships/accounts/${connection.account_id}`}
                          className="text-primary hover:underline"
                          prefetch={false}
                        >
                          {accountName}
                        </Link>
                      ) : (
                        "Unassigned"
                      ),
                    },
                    {
                      label: "Enabled",
                      value: <StatusBadge value={connection.enabled ? "enabled" : "disabled"} />,
                    },
                    { label: "API", value: <DataValue value={connection.api_url} /> },
                    {
                      label: "Sync interval",
                      value: partnerSyncInterval(connection.sync_interval_minutes),
                    },
                  ]}
                />
                <InfoPanel
                  title="Products"
                  fields={[
                    {
                      label: "Synced",
                      value: (
                        <Link
                          className="text-blue-700 dark:text-blue-400 hover:underline"
                          prefetch={false}
                          href={`${partnerHref(connection.provider)}/related`}
                        >
                          {connection.products}
                        </Link>
                      ),
                    },
                    ...[
                      ["Qualified", connection.qualified],
                      ["Checking", connection.checking],
                      ["Needs attention", connection.needs_attention],
                      ["Excluded", connection.excluded],
                    ].map(([label, value]) => ({
                      label: String(label),
                      value: <DataValue value={value} />,
                    })),
                  ]}
                />
              </div>
              <div className="space-y-6">
                <InfoPanel
                  title="Record information"
                  fields={[
                    { label: "Provider", value: <DataValue value={connection.provider} /> },
                    { label: "Revision", value: <DataValue value={connection.revision} /> },
                  ]}
                />
                <InfoPanel
                  title="Sync status"
                  fields={[
                    {
                      label: "Status",
                      value: (
                        <StatusBadge value={!connection.enabled ? "paused" : connection.state} />
                      ),
                    },
                    {
                      label: "Last synced",
                      value: connection.last_sync_at ? (
                        <DateTime value={connection.last_sync_at} />
                      ) : (
                        "Not synced yet"
                      ),
                    },
                    {
                      label: "Next sync",
                      value: connection.next_sync_at ? (
                        <DateTime value={connection.next_sync_at} />
                      ) : (
                        "Not scheduled"
                      ),
                    },
                    { label: "Last error", value: <DataValue value={connection.error} /> },
                  ]}
                />
                {connection.enabled && !connection.ai_enabled && (
                  <p role="status" className="text-sm text-muted-foreground">
                    Automatic product checks are waiting for AI to be enabled in settings.
                  </p>
                )}
              </div>
            </div>
          )}
        </>
      )}
    </section>
  );
}
