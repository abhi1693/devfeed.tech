"use client";

import { useState } from "react";
import { Button } from "@/components/atoms/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/atoms/table";
import { Metric } from "@/components/molecules/metric";
import { adminOverview } from "@/lib/api/generated/admin";
import type { AdminOverview } from "@/lib/api/generated/models";
import { notify, notifyFailure } from "@/lib/notifications";

export function Overview({ initialData }: { initialData: AdminOverview }) {
  const [data, setData] = useState(initialData);
  const [loading, setLoading] = useState(false);
  async function refresh() {
    try { setData(await adminOverview()); notify.success("Overview refreshed"); }
    catch (error) { notifyFailure(error, "Could not refresh overview"); }
    finally { setLoading(false); }
  }

  return <section className="space-y-6" aria-busy={loading}>
    <div className="flex items-start justify-between gap-4">
      <div><h1 className="text-2xl font-semibold tracking-tight">Overview</h1>
        <p className="mt-1 text-sm text-muted-foreground">Content and review status.</p></div>
      <Button variant="outline" size="sm" loading={loading} loadingText="Loading…" onClick={() => {
        setLoading(true); void refresh();
      }}>
        Refresh
      </Button>
    </div>
    {data && <>
      <div className="grid gap-4 sm:grid-cols-3">
        <Metric label="Articles" value={data.articles} />
        <Metric label="Sources" value={data.sources} />
        <Metric label="Topics" value={data.topics} />
      </div>
      <div className="rounded-md border bg-card">
        <Table>
          <TableHeader><TableRow><TableHead>Status</TableHead><TableHead className="text-right">Count</TableHead></TableRow></TableHeader>
          <TableBody>
            {[
              ["Articles awaiting review", data.articles_pending_review],
              ["Sources awaiting review", data.sources_pending_review],
              ["Published articles", data.articles_published],
            ].map(([label, count]) => <TableRow key={label}>
              <TableCell>{label}</TableCell><TableCell className="text-right tabular-nums">{count.toLocaleString("en")}</TableCell>
            </TableRow>)}
          </TableBody>
        </Table>
      </div>
      <p className="text-xs text-muted-foreground">Review and editing screens will be added next. No content is changed from this screen.</p>
    </>}
  </section>;
}
