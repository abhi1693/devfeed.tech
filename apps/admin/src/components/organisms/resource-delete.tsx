"use client";
import { resourceTrail, recordHref, resourceHref } from "@/lib/routes";
import Link from "next/link";
import { useCallback, useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/atoms/button";
import { Field } from "@/components/molecules/field";
import { TopicReplacementPicker } from "@/components/molecules/topic-replacement-picker";
import { DeleteConfirmation } from "@/components/molecules/delete-confirmation";
import {
  DeletionImpactTable,
  type DeletionImpact,
} from "@/components/molecules/deletion-impact-table";
import { adminRouteTitle } from "@/lib/page-titles";
import { PageHeading } from "@/components/molecules/page-heading";
import { RequestState } from "@/components/molecules/request-state";
import { useAdmin } from "@/components/molecules/admin-session";
import { getRecord, deleteRecord } from "@/lib/resource-api";
import { type Resource, resources } from "@/lib/resources";
import { useRequest } from "@/lib/use-request";
import { notify, notifyFailure } from "@/lib/notifications";
import { adminTopicDeletePreview } from "@/lib/api/generated/admin";
export function ResourceDelete({ resource, id }: { resource: Resource; id: string }) {
  const admin = useAdmin();
  const router = useRouter();
  const spec = resources[resource];
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const [replacementTopicId, setReplacementTopicId] = useState("");
  const load = useCallback(
    async (signal: AbortSignal) => {
      const [record, impact] = await Promise.all([
        getRecord(resource, id, signal),
        resource === "topics" ? adminTopicDeletePreview(id, { signal }) : Promise.resolve(null),
      ]);
      return { record, impact };
    },
    [resource, id],
  );
  const result = useRequest(`${resource}/${id}`, load);
  const href = recordHref(resource, { id });
  const impact = result.data?.impact;
  const counts: DeletionImpact[] | undefined = impact
    ? [
        {
          label: "Linked articles",
          count: impact.articles,
          action: replacementTopicId ? "Unpublish and relink" : "Unpublish and unlink",
        },
        {
          label: "Currently published",
          count: impact.published_articles,
          action: "Remove from public feed",
        },
        {
          label: "Tag links",
          count: impact.tags,
          action: replacementTopicId ? "Relink" : "Unlink",
        },
        { label: "Relationships", count: impact.relationships, action: "Delete" },
        { label: "Relationship proposals", count: impact.relationship_proposals, action: "Delete" },
        { label: "Relationship research runs", count: impact.research_jobs, action: "Delete" },
        {
          label: "Pending topic proposals",
          count: impact.pending_topic_proposals,
          action: "Reject",
        },
      ]
    : undefined;
  async function remove(event: React.FormEvent) {
    event.preventDefault();
    if (confirmation !== "DELETE" || busy) return;
    setBusy(true);
    try {
      if (resource === "topics" && replacementTopicId)
        await deleteRecord(resource, id, admin.csrf_token, replacementTopicId);
      else await deleteRecord(resource, id, admin.csrf_token);
      notify.success(`${spec.singular} deleted`);
      router.replace(resourceHref(resource));
      router.refresh();
    } catch (error) {
      notifyFailure(error, `Could not delete ${spec.singular.toLowerCase()}`);
      setBusy(false);
    }
  }
  return (
    <section className="max-w-2xl space-y-6">
      <PageHeading
        title={`Delete ${spec.singular.toLowerCase()}`}
        browserTitle={adminRouteTitle(
          { view: "delete", resource, id },
          result.data?.record[spec.title],
        )}
        trail={[...resourceTrail(resource), { label: spec.label, href: resourceHref(resource) }]}
      />
      <RequestState loading={result.loading} error={result.error} />
      {result.data && (
        <form
          onSubmit={remove}
          className="space-y-5 rounded-lg border border-destructive/30 bg-card p-6"
        >
          <h2 className="break-words font-semibold">{String(result.data.record[spec.title])}</h2>
          <p className="text-sm text-muted-foreground">
            {resource === "topics"
              ? "This permanently deletes the topic. Linked articles are kept, unpublished, and require review before publishing again."
              : resource === "sources"
                ? "This permanently deletes the source. Articles are kept; they remain visible in the public feed only if another approved source is linked."
                : "This permanently deletes this record. It cannot be undone. Linked content or active jobs can prevent deletion. Disable or unpublish records when you want to retain their history."}
          </p>
          {resource === "articles" && (
            <p className="text-sm text-muted-foreground">
              Its source links, classification links, extracted evidence, completed jobs, and review
              history will also be removed. The RSS feed may ingest it again; reject it to keep it
              excluded instead.
            </p>
          )}
          {resource === "sources" && (
            <p className="text-sm text-muted-foreground">
              Source links, follows, review history, and all source jobs will be removed, including
              queued and running jobs. This cannot be undone.
            </p>
          )}
          <DeleteConfirmation value={confirmation} onChange={setConfirmation} disabled={busy}>
            {resource === "topics" && (
              <Field
                label="Replacement topic"
                disabled={busy}
                subtext="Optional. Transfer article and tag links to another topic or pending proposal. Pending proposals stay inactive until approved. Leave empty to remove the links."
              >
                {(control) => (
                  <TopicReplacementPicker
                    {...control}
                    topicId={id}
                    value={replacementTopicId}
                    onChange={setReplacementTopicId}
                  />
                )}
              </Field>
            )}
            {counts && <DeletionImpactTable impact={counts} />}
          </DeleteConfirmation>
          <div className="flex flex-wrap justify-end gap-2">
            <Button variant="outline" asChild>
              <Link href={href} prefetch={false}>
                Cancel
              </Link>
            </Button>
            <Button
              variant="destructive"
              type="submit"
              disabled={confirmation !== "DELETE"}
              loading={busy}
              loadingText="Deleting…"
            >
              Delete permanently
            </Button>
          </div>
        </form>
      )}
    </section>
  );
}
