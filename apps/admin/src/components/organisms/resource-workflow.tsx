"use client";
import { resourceTrail, recordHref, resourceHref } from "@/lib/routes";
import Link from "next/link";
import { useCallback, useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/atoms/button";
import { FormField } from "@/components/molecules/form-field";
import { adminRouteTitle } from "@/lib/page-titles";
import { PageHeading } from "@/components/molecules/page-heading";
import { RequestState } from "@/components/molecules/request-state";
import { useAdmin } from "@/components/molecules/admin-session";
import { getRecord, type RecordData } from "@/lib/resource-api";
import { resources, humanize } from "@/lib/resources";
import { useRequest } from "@/lib/use-request";
import { adminArticleReview, adminSourceReview, adminSourceFetch } from "@/lib/api/generated/admin";
import type { AdminArticleOut, ReviewArticle, ReviewSource } from "@/lib/api/generated/models";
import { ClassificationForm } from "./classification-form";
import { ValidationErrors } from "@/components/molecules/validation-errors";
import { notify, notifyFailure } from "@/lib/notifications";
import { StatusBadge } from "@/components/molecules/status-badge";
export type Workflow = "review" | "classify" | "fetch";
export function ResourceWorkflow({ resource, id, action }: { resource: "articles" | "sources"; id: string; action: Workflow }) {
  const load = useCallback((signal: AbortSignal) => getRecord(resource, id, signal), [resource, id]);
  const result = useRequest(`${resource}/${id}`, load);
  return <section className="max-w-4xl space-y-6"><PageHeading browserTitle={adminRouteTitle({ view: "workflow", resource, id, action }, result.data?.[resources[resource].title])} title={`${humanize(action)} ${resources[resource].singular.toLowerCase()}`} trail={[...resourceTrail(resource), { label: resources[resource].label, href: resourceHref(resource) }, { label: result.data ? String(result.data[resources[resource].title]) : "Object", href: recordHref(resource, { id }) }]} /><RequestState loading={result.loading} error={result.error} />{result.data && (action === "classify" ? <ClassificationForm article={result.data as unknown as AdminArticleOut} /> : <Decision resource={resource} record={result.data} action={action} />)}<Button variant="outline" asChild><Link prefetch={false} href={recordHref(resource, { id })}>Back to object</Link></Button></section>;
}
function Decision({ resource, record, action }: { resource: "articles" | "sources"; record: RecordData; action: "review" | "fetch" }) {
  const admin = useAdmin(); const router = useRouter();
  const [decision, setDecision] = useState(resource === "articles" ? "approve" : "approved"); const [note, setNote] = useState(""); const [busy, setBusy] = useState(false); const [error, setError] = useState<Error>();
  async function submit(event: React.FormEvent) { event.preventDefault(); if (busy) return; setBusy(true); setError(undefined); const options = { headers: { "X-CSRF-Token": admin.csrf_token } };
    try { if (action === "fetch") { const job = await adminSourceFetch(record.id, options); notify.success("Feed fetch requested", { description: "Open the run to follow its progress." }); router.replace(`/jobs/ingestion/${job.id}`); }
      else { if (resource === "articles") await adminArticleReview(record.id, { action: decision as ReviewArticle["action"], expected_revision: Number(record.editorial_revision), note: note || null }, options);
        else await adminSourceReview(record.id, { decision: decision as ReviewSource["decision"], note: note || null }, options);
        const outcome = ({ approve: "approved", reject: "rejected", publish: "published", unpublish: "unpublished" } as Record<string, string>)[decision] ?? decision;
        notify.success(`${resources[resource].singular} ${outcome}`);
        router.replace(recordHref(resource, record)); } router.refresh();
    } catch (error) { setError(error instanceof Error ? error : undefined); notifyFailure(error, action === "fetch" ? "Could not request feed fetch" : "Could not apply decision"); setBusy(false); }
  }
  return <form onSubmit={submit} className="space-y-6 rounded-lg border bg-card p-6"><ValidationErrors error={error} />{action === "fetch" ? <p className="text-sm">Queue a feed fetch for <strong>{String(record.name)}</strong>. The source must be approved and enabled. An existing queued or running request is reused; the scheduler dispatches it to a worker.</p> : <>
    {resource === "articles" && <div className="space-y-2 text-sm"><p>Current review: <StatusBadge value={record.review_status} /> · Publication: <StatusBadge value={record.publication_status} /></p><p className="text-muted-foreground">Approval and publication are separate decisions. Publication still requires complete metadata, an active primary topic, and an approved source.</p>{(record.publication_blockers as string[]).length > 0 && <ul className="list-inside list-disc text-xs text-muted-foreground">{(record.publication_blockers as string[]).map(reason => <li key={reason}>{humanize(reason)}</li>)}</ul>}</div>}
    <FormField field={{ key: "decision", label: "Decision", type: "select", required: true, choices: resource === "articles" ? ["approve", "reject", "publish", "unpublish"] : ["approved", "rejected"] }} value={decision} onChange={value => setDecision(String(value))} disabled={busy} />
    <FormField field={{ key: "note", label: "Reason / note", type: "textarea", max: 1000, required: decision === "reject" || decision === "rejected", help: "A reason is required for rejection. Your authenticated identity is recorded automatically." }} value={note} onChange={value => setNote(String(value))} disabled={busy} />
  </>}<Button type="submit" loading={busy} loadingText="Submitting…">{action === "fetch" ? "Queue fetch" : "Apply decision"}</Button></form>;
}
