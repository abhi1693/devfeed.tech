"use client";

import { partnerSyncInterval } from "@/lib/partner-interval";

import { useRefreshInterval } from "@/lib/use-refresh-interval";

import Link from "next/link";
import { Pencil, RefreshCw } from "lucide-react";
import { ResourcePanel, DataValue } from "@devfeed/ui/resource-layout";
import { ConnectionLogo } from "@devfeed/ui/connection-logo";
import { DateTime } from "@/components/molecules/date-time";
import { StatusBadge } from "@/components/molecules/status-badge";
import { PartnerRelated } from "./partner-related";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Button } from "@devfeed/ui/button";
import { ResourceHeading } from "@devfeed/ui/resource-layout";
import { RequestState } from "@/components/molecules/request-state";
import { useAdmin } from "@/components/molecules/admin-session";
import { PartnerConnectionForm } from "./partner-connection-form";
import {
  importConnectionHref,
  importConnectionsPath,
  partnershipTrail,
} from "./partner-connections";
import {
  dashboardV1AdminPartnerAccountsAccountIdDashboardGet,
  adminPartnerConnectionGet,
  adminPartnerProvidersList,
  adminPartnerConnectionAction,
} from "@/lib/api/generated/admin";
import type { AccountOut, ConnectionOut, PartnerProviderOut } from "@/lib/api/generated/models";
import { ApiError } from "@/lib/api/client";

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
  const [account, setAccount] = useState<AccountOut>();
  const [connection, setConnection] = useState<ConnectionOut>();
  const [providers, setProviders] = useState<PartnerProviderOut[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error>();
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    let live = true;
    async function findConnection(identifier: string) {
      try {
        return await adminPartnerConnectionGet(identifier);
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) return undefined;
        throw error;
      }
    }
    async function load() {
      try {
        const [found, supported] = await Promise.all([
          provider ? findConnection(provider) : undefined,
          !provider || editing ? adminPartnerProvidersList() : [],
        ]);
        if (provider && !found) throw new Error("Import connection not found.");
        // Presets are a small fixed catalog. Probe each identity directly so an
        // existing preset outside the connections page cannot be offered again.
        const existing = provider
          ? []
          : await Promise.all(supported.map((preset) => findConnection(preset.provider)));
        const available = provider
          ? supported
          : supported.filter((_preset, index) => !existing[index]);
        const dashboard =
          found?.account_id && !editing && section === "details"
            ? await dashboardV1AdminPartnerAccountsAccountIdDashboardGet(found.account_id, {
                limit: 1,
              })
            : undefined;
        if (live) {
          setConnection(found);
          setAccount(dashboard?.account);
          setProviders(available);
          setError(undefined);
        }
      } catch (error) {
        if (live)
          setError(error instanceof Error ? error : new Error("Could not load import connection."));
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
    { label: "Import connections", href: importConnectionsPath },
    ...(editing && connection
      ? [{ label: connection.name, href: importConnectionHref(connection.provider) }]
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
      setError(error instanceof Error ? error : new Error("Could not sync import connection."));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="space-y-6">
      <ResourceHeading
        title={
          !provider
            ? "Create import connection"
            : editing
              ? `Edit ${connection?.name ?? "import connection"}`
              : (connection?.name ?? "Import connection")
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
              <Link href={`${importConnectionHref(connection.provider)}/edit`} prefetch={false}>
                <Pencil aria-hidden />
                Edit
              </Link>
            </Button>
          </>
        )}
      </ResourceHeading>
      <RequestState loading={loading} error={error} retry={() => setRefresh((n) => n + 1)} />
      {!loading && !error && (!provider || editing) && (
        <PartnerConnectionForm
          providers={providers}
          connection={connection}
          onSaved={(saved) => router.push(importConnectionHref(saved.provider))}
          onCancel={() =>
            router.push(
              connection ? importConnectionHref(connection.provider) : importConnectionsPath,
            )
          }
        />
      )}
      {connection && !editing && (
        <>
          <nav aria-label="Object sections" className="flex gap-5 overflow-x-auto border-b">
            {(["details", "related"] as const).map((tab) => (
              <Link
                key={tab}
                prefetch={false}
                href={
                  importConnectionHref(connection.provider) + (tab === "details" ? "" : "/related")
                }
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
                <ResourcePanel
                  title="Import connection"
                  fields={[
                    {
                      label: "Name",
                      value: (
                        <span className="inline-flex items-center gap-3">
                          <ConnectionLogo
                            name={connection.name}
                            url={connection.logo_url}
                            variants={connection.logo_variants}
                          />
                          <DataValue value={connection.name} />
                        </span>
                      ),
                    },
                    { label: "Type", value: "Launch platform" },
                    {
                      label: "Partner account",
                      value: connection.account_id ? (
                        <Link
                          href={`/partnerships/accounts/${connection.account_id}`}
                          className="text-primary hover:underline"
                          prefetch={false}
                        >
                          {account?.name}
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
                {account && (
                  <ResourcePanel
                    title="Inherited promotion policy"
                    fields={[
                      {
                        label: "Default promotion",
                        value: `${account.effective_promotion_kind === "ad" ? "Sponsored" : "Product recommendation"} · ${account.promotion_kind_source === "account" ? "account override" : "inherited from tier"}`,
                      },
                      {
                        label: "Imported product acceptance",
                        value:
                          account.approval_mode === "trusted"
                            ? "Trusted partner imports accepted automatically"
                            : "Automatic capability checks",
                      },
                      {
                        label: "Product click destination",
                        value:
                          account.outbound_destination === "listing_url"
                            ? "Partner listing · DevFeed UTM"
                            : "Product website · DevFeed UTM",
                      },
                    ]}
                  >
                    <p className="py-3 text-sm text-muted-foreground">
                      Existing and newly imported products follow these account settings
                      automatically. Contextual matches are preferred; sponsored feed placements can
                      fill unmatched feeds. Article and topic placements require a relevant match.
                    </p>
                    <Link
                      href={`/partnerships/accounts/${account.id}/edit`}
                      prefetch={false}
                      className="text-sm text-primary hover:underline"
                    >
                      Edit partner account defaults
                    </Link>
                  </ResourcePanel>
                )}
                <ResourcePanel
                  title="Products"
                  fields={[
                    {
                      label: "Synced",
                      value: (
                        <Link
                          className="text-blue-700 dark:text-blue-400 hover:underline"
                          prefetch={false}
                          href={`${importConnectionHref(connection.provider)}/related`}
                        >
                          {connection.products}
                        </Link>
                      ),
                    },
                    ...[
                      ["Accepted for account", connection.commercial_approved ?? 0],
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
                <ResourcePanel
                  title="Record information"
                  fields={[
                    {
                      label: "Platform identifier",
                      value: <DataValue value={connection.provider} />,
                    },
                    { label: "Revision", value: <DataValue value={connection.revision} /> },
                  ]}
                />
                <ResourcePanel
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
