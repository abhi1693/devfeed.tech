"use client";
import { resourceTrail, recordHref, resourceHref } from "@/lib/routes";
import Link from "next/link";
import { useCallback, useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/atoms/button";
import { Input } from "@/components/atoms/input";
import { Field } from "@/components/molecules/field";
import { PageHeading } from "@/components/molecules/page-heading";
import { RequestState } from "@/components/molecules/request-state";
import { useAdmin } from "@/components/molecules/admin-session";
import { getRecord, deleteRecord } from "@/lib/resource-api";
import { type Resource, resources } from "@/lib/resources";
import { useRequest } from "@/lib/use-request";
import { notify, notifyFailure } from "@/lib/notifications";
export function ResourceDelete({ resource, id }: { resource: Resource; id: string }) {
  const admin = useAdmin(); const router = useRouter(); const spec = resources[resource];
  const [confirmation, setConfirmation] = useState(""); const [busy, setBusy] = useState(false);
  const load = useCallback((signal: AbortSignal) => getRecord(resource, id, signal), [resource, id]);
  const result = useRequest(`${resource}/${id}`, load); const href = recordHref(resource, { id });
  async function remove(event: React.FormEvent) {
    event.preventDefault(); if (confirmation !== "DELETE" || busy) return; setBusy(true);
    try {
      await deleteRecord(resource, id, admin.csrf_token);
      notify.success(`${spec.singular} deleted`);
      router.replace(resourceHref(resource)); router.refresh();
    } catch (error) { notifyFailure(error, `Could not delete ${spec.singular.toLowerCase()}`); setBusy(false); }
  }
  return <section className="max-w-2xl space-y-6">
    <PageHeading title={`Delete ${spec.singular.toLowerCase()}`} trail={[...resourceTrail(resource), { label: spec.label, href: resourceHref(resource) }]} />
    <RequestState loading={result.loading} error={result.error} />
    {result.data && <form onSubmit={remove} className="space-y-5 rounded-lg border border-destructive/30 bg-card p-6">
      <h2 className="break-words font-semibold">{String(result.data[spec.title])}</h2>
      <p className="text-sm text-muted-foreground">This permanently deletes this record. It cannot be undone. Linked content or active jobs can prevent deletion. Disable or unpublish records when you want to retain their history.</p>
      {resource === "articles" && <p className="text-sm text-muted-foreground">Its source links, classification links, extracted evidence, completed jobs, and review history will also be removed. The RSS feed may ingest it again; reject it to keep it excluded instead.</p>}
      {resource === "sources" && <p className="text-sm text-muted-foreground">Completed job records and review history will be removed. Sources with linked articles or active jobs cannot be deleted.</p>}
      <Field id="confirm-delete" label="Type DELETE to confirm" required disabled={busy}>{control => <Input {...control} value={confirmation} onChange={event => setConfirmation(event.target.value)} autoComplete="off" />}</Field>
      <div className="flex gap-2"><Button variant="destructive" type="submit" disabled={confirmation !== "DELETE"} loading={busy} loadingText="Deleting…">Delete permanently</Button><Button variant="outline" asChild><Link href={href} prefetch={false}>Cancel</Link></Button></div>
    </form>}
  </section>;
}
