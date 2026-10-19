"use client";
import { resourceTrail, recordHref, resourceHref } from "@/lib/routes";
import Link from "next/link";
import { useCallback, useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@devfeed/ui/button";
import { FormField } from "@/components/molecules/form-field";
import { adminRouteTitle } from "@/lib/page-titles";
import { ResourceHeading } from "@devfeed/ui/resource-layout";
import { RequestState } from "@/components/molecules/request-state";
import { useAdmin } from "@/components/molecules/admin-session";
import { getRecord, type RecordData } from "@/lib/resource-api";
import { recordIdentityKey, type RecordIdentity } from "@/lib/record-identities";
import { resources, humanize } from "@/lib/resources";
import { useRequest } from "@/lib/use-request";
import { adminArticleReview, adminSourceReview, adminSourceFetch } from "@/lib/api/generated/admin";
import type { AdminArticleOut, ReviewArticle, ReviewSource } from "@/lib/api/generated/models";
import { ClassificationForm } from "./classification-form";
import { ValidationErrors } from "@/components/molecules/validation-errors";
import { notify, notifyFailure } from "@/lib/notifications";
import { StatusBadge } from "@/components/molecules/status-badge";
import { SourceReadiness } from "./source-readiness";
import type { SourcePipelineDetail } from "@/lib/source-pipeline";
import { useBoardRefreshInterval } from "@/lib/use-refresh-interval";
import { getArticlePipelineDetail } from "@/lib/article-pipeline";
import { DateTime } from "@/components/molecules/date-time";
type WorkflowIdentity = Extract<RecordIdentity, { resource: "articles" | "sources" }>;
export type Workflow = "review" | "classify" | "fetch";
export function ResourceWorkflow({
  identity,
  action,
}: {
  identity: WorkflowIdentity;
  action: Workflow;
}) {
  const resource = identity.resource;
  const seconds = useBoardRefreshInterval();
  const [revision, setRevision] = useState(0);
  const [sourceReadiness, setSourceReadiness] = useState<SourcePipelineDetail>();
  const load = useCallback((signal: AbortSignal) => getRecord(identity, signal), [identity]);
  const result = useRequest(
    recordIdentityKey(identity),
    load,
    resource === "sources" || action === "review" ? seconds * 1000 : 0,
    revision,
  );
  return (
    <section className="max-w-4xl space-y-6">
      <ResourceHeading
        title={`${humanize(action)} ${resources[resource].singular.toLowerCase()}`}
        browserTitle={adminRouteTitle(
          { view: "workflow", identity, action },
          result.data?.[resources[resource].title],
        )}
        trail={[
          ...resourceTrail(resource),
          { label: resources[resource].label, href: resourceHref(resource) },
          {
            label: result.data ? String(result.data[resources[resource].title]) : "Object",
            href: recordHref(identity),
          },
        ]}
      />
      <RequestState
        loading={result.loading}
        error={result.error}
        retry={() => setRevision((value) => value + 1)}
      />
      {resource === "sources" && (
        <SourceReadiness
          id={identity.id}
          onChange={setSourceReadiness}
          onUpdated={() => setRevision((value) => value + 1)}
        />
      )}
      {result.data &&
        (action === "classify" ? (
          <ClassificationForm article={result.data as unknown as AdminArticleOut} />
        ) : (
          <Decision
            identity={identity}
            record={result.data}
            action={action}
            sourceReadiness={sourceReadiness?.item.id === identity.id ? sourceReadiness : undefined}
          />
        ))}
      <Button variant="outline" asChild>
        <Link prefetch={false} href={recordHref(identity)}>
          Back to object
        </Link>
      </Button>
    </section>
  );
}
function Decision({
  identity,
  record,
  action,
  sourceReadiness,
}: {
  identity: WorkflowIdentity;
  record: RecordData;
  action: "review" | "fetch";
  sourceReadiness?: SourcePipelineDetail;
}) {
  const resource = identity.resource;
  const admin = useAdmin();
  const router = useRouter();
  const [decision, setDecision] = useState(resource === "articles" ? "approve" : "approved");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error>();
  const [readinessRevision, setReadinessRevision] = useState(0);
  const refreshSeconds = useBoardRefreshInterval();
  const readinessLoad = useCallback(
    async (signal: AbortSignal) =>
      resource === "articles"
        ? {
            ...(await getArticlePipelineDetail(identity.id, signal)),
            receivedAt: new Date().toISOString(),
          }
        : null,
    [resource, identity.id],
  );
  const readiness = useRequest(
    `review-readiness/${resource}/${identity.id}/${readinessRevision}`,
    readinessLoad,
    resource === "articles" ? refreshSeconds * 1000 : 0,
  );
  const publicationBlocked =
    resource === "articles" &&
    decision === "publish" &&
    (!readiness.data?.can_publish ||
      readiness.data.editorial_revision !== Number(record.editorial_revision));
  const sourceBlocked =
    resource === "sources" &&
    (action === "fetch"
      ? !sourceReadiness?.item.can_ingest
      : decision === "approved" && !sourceReadiness?.item.can_approve);
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (busy || sourceBlocked || publicationBlocked) return;
    setBusy(true);
    setError(undefined);
    const options = { headers: { "X-CSRF-Token": admin.csrf_token } };
    try {
      if (action === "fetch") {
        const job = await adminSourceFetch(identity.id, options);
        notify.success("Feed fetch requested", {
          description: "Open the run to follow its progress.",
        });
        router.replace(recordHref({ resource: "ingestion-jobs", id: job.id }));
      } else {
        if (resource === "articles")
          await adminArticleReview(
            identity.id,
            {
              action: decision as ReviewArticle["action"],
              expected_revision: Number(record.editorial_revision),
              note: note || null,
            },
            options,
          );
        else
          await adminSourceReview(
            identity.id,
            { decision: decision as ReviewSource["decision"], note: note || null },
            options,
          );
        const outcome =
          (
            {
              approve: "approved",
              reject: "rejected",
              publish: "published",
              unpublish: "unpublished",
            } as Record<string, string>
          )[decision] ?? decision;
        notify.success(`${resources[resource].singular} ${outcome}`);
        router.replace(recordHref(identity));
      }
      router.refresh();
    } catch (error) {
      setError(error instanceof Error ? error : undefined);
      notifyFailure(
        error,
        action === "fetch" ? "Could not request feed fetch" : "Could not apply decision",
      );
      setBusy(false);
    }
  }
  return (
    <form onSubmit={submit} className="space-y-6 rounded-lg border bg-card p-6">
      <ValidationErrors error={error} />
      {action === "fetch" ? (
        <p className="text-sm">
          Check <strong>{String(record.name)}</strong> for new articles. The source must be approved
          and enabled.
        </p>
      ) : (
        <>
          {resource === "articles" && (
            <div className="space-y-2 text-sm">
              <p>
                Current review: <StatusBadge value={record.review_status} /> · Publication:{" "}
                <StatusBadge value={record.publication_status} />
              </p>
              <p className="text-muted-foreground">
                Approval and publication are separate decisions. Enrichment, AI analysis, and image
                processing must finish before publication, alongside the editorial and source
                requirements.
              </p>
              <p className="text-xs text-muted-foreground">
                Updates every {refreshSeconds} seconds while visible.
                {readiness.data && (
                  <>
                    {" "}
                    Updated <DateTime value={readiness.data.receivedAt} />.
                  </>
                )}
              </p>
              {(record.publication_blockers as string[]).length > 0 && (
                <ul className="list-inside list-disc text-xs text-muted-foreground">
                  {(record.publication_blockers as string[]).map((reason) => (
                    <li key={reason}>{humanize(reason)}</li>
                  ))}
                </ul>
              )}
              {decision === "publish" && (
                <>
                  <RequestState
                    loading={readiness.loading}
                    error={readiness.error}
                    retry={() => setReadinessRevision((value) => value + 1)}
                  />
                  {publicationBlocked && !readiness.loading && (
                    <p role="status" className="text-sm text-muted-foreground">
                      Publication is not ready.{" "}
                      <Link className="text-primary underline" href={recordHref(identity)}>
                        Open publication readiness
                      </Link>{" "}
                      for the current processing requirements.
                    </p>
                  )}
                  {!!readiness.data?.blockers.length && (
                    <ul className="list-inside list-disc text-xs text-muted-foreground">
                      {readiness.data.blockers.map((reason) => (
                        <li key={reason}>{humanize(reason)}</li>
                      ))}
                    </ul>
                  )}
                </>
              )}
            </div>
          )}
          <FormField
            field={{
              key: "decision",
              label: "Decision",
              type: "select",
              required: true,
              choices:
                resource === "articles"
                  ? ["approve", "reject", "publish", "unpublish"]
                  : ["approved", "rejected"],
            }}
            value={decision}
            onChange={(value) => setDecision(String(value))}
            disabled={busy}
          />
          <FormField
            field={{
              key: "note",
              label: "Reason / note",
              type: "textarea",
              max: 1000,
              required: decision === "reject" || decision === "rejected",
              help: "A reason is required for rejection. Your authenticated identity is recorded automatically.",
            }}
            value={note}
            onChange={(value) => setNote(String(value))}
            disabled={busy}
          />
        </>
      )}
      {sourceBlocked && (
        <p className="text-sm text-muted-foreground">
          {!sourceReadiness
            ? "Checking source readiness…"
            : action === "fetch"
              ? "Resolve the source readiness blockers or resume ingestion before fetching."
              : "Complete the source profile and branding before approving. Manual rejection remains available."}
        </p>
      )}
      <Button
        type="submit"
        disabled={sourceBlocked || publicationBlocked}
        loading={busy}
        loadingText="Submitting…"
      >
        {action === "fetch" ? "Queue fetch" : "Apply decision"}
      </Button>
    </form>
  );
}
