"use client";

import { useState } from "react";
import Link from "next/link";
import { Button } from "@/components/atoms/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/atoms/card";
import { useAdmin } from "@/components/molecules/admin-session";
import { adminAutomationRecover } from "@/lib/api/generated/admin";
import type { AutomationOverview as AutomationData, AutomationBlocker, RecoveryTarget } from "@/lib/api/generated/models";
import { notify, notifyFailure } from "@/lib/notifications";

const actionLabels = { enrich: "Fetch article text", analyze: "Analyze again", evaluate: "Evaluate source policy" };

export function AutomationOverview({ data, onChange }: { data: AutomationData; onChange: () => void }) {
  const admin = useAdmin();
  const [busy, setBusy] = useState<string | null>(null);
  async function recover(group: AutomationBlocker, target: RecoveryTarget) {
    if (!group.action || busy) return;
    setBusy(target.id);
    try {
      const result = await adminAutomationRecover(target.id, group.action, { expected_revision: target.revision ?? 0 }, { headers: { "X-CSRF-Token": admin.csrf_token } });
      if (result.status === "queued") notify.success("Recovery job queued");
      else if (result.status === "published") notify.success("Article published by its source policy");
      else if (result.status === "would_publish") notify.success("Preview passed; publication remains in preview mode");
      else notify.warning("Article still needs review", { description: "Open the article's publication decisions for the current reasons." });
      onChange();
    } catch (error) { notifyFailure(error, "Could not complete recovery"); }
    finally { setBusy(null); }
  }
  const median = data.median_ingestion_to_publication_seconds;
  return <Card className="shadow-none">
    <CardHeader><CardTitle>Automation and blockers</CardTitle><CardDescription>Current blockers can overlap. Actions apply to the selected article and preserve editorial decisions.</CardDescription></CardHeader>
    <CardContent className="space-y-5">
      <dl className="grid gap-4 sm:grid-cols-3">
        <div><dt className="text-xs text-muted-foreground">Published without intervention</dt><dd className="mt-1 text-xl font-semibold">{data.automatic_publication_percent == null ? "—" : `${data.automatic_publication_percent}%`}</dd><p className="text-xs text-muted-foreground">{data.published_without_intervention} of {data.published_in_window} publications in this period</p></div>
        <div><dt className="text-xs text-muted-foreground">Median time to publication</dt><dd className="mt-1 text-xl font-semibold">{median == null ? "—" : `${Math.round(median / 60).toLocaleString("en")} min`}</dd><p className="text-xs text-muted-foreground">From discovery to first publication</p></div>
        <div><dt className="text-xs text-muted-foreground">Reported AI tokens</dt><dd className="mt-1 text-xl font-semibold">{data.analysis_tokens.toLocaleString("en")}</dd><p className="text-xs text-muted-foreground">{data.usage_reported_runs} finished runs with usage data; not a billing total</p></div>
      </dl>
      <div className="divide-y">{data.blockers.map(group => <div key={group.code} className="py-4 first:pt-0 last:pb-0">
        <h3 className="text-sm font-medium">{group.label} <span className="ml-2 text-muted-foreground">{group.count.toLocaleString("en")}</span></h3>
        {group.targets.length > 0 && <ul className="mt-2 space-y-2">{group.targets.map(target => <li key={target.id} className="flex flex-wrap items-center justify-between gap-2 text-sm">
          <Link className="min-w-0 flex-1 break-words underline underline-offset-4" href={target.kind === "article" ? `/content/articles/${target.id}` : target.kind === "relationship-proposal" ? `/taxonomy/relationships/proposals/${target.id}` : `/taxonomy/topics/proposals/${target.id}`}>{target.title}</Link>
          {group.action && <Button size="sm" variant="outline" disabled={busy !== null} loading={busy === target.id} onClick={() => void recover(group, target)}>{actionLabels[group.action]}</Button>}
        </li>)}</ul>}
        {group.count > group.targets.length && <p className="mt-2 text-xs text-muted-foreground">Showing the five oldest records. Refresh after resolving them to see the next records.</p>}
      </div>)}</div>
    </CardContent>
  </Card>;
}
