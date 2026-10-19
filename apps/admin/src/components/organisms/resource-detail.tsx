"use client";
import { Markdown } from "@devfeed/ui/markdown";

import { DateTime } from "@/components/molecules/date-time";
import {
  resourceHref,
  detailSections,
  resourceTrail,
  recordHref,
  type DetailSection,
  type UserSection,
} from "@/lib/routes";
import Link from "next/link";
import { Sparkles } from "lucide-react";
import { Button } from "@devfeed/ui/button";
import { useCallback, useState } from "react";
import { adminRouteTitle } from "@/lib/page-titles";
import { ResourceHeading } from "@devfeed/ui/resource-layout";
import { RecordActions } from "@/components/molecules/record-actions";
import { RecordLink } from "@/components/molecules/record-link";
import { ImageJobSubject, imageJobOperation } from "@/components/molecules/image-job-subject";
import { ResourcePanel, DataValue } from "@devfeed/ui/resource-layout";
import { ImagePreviewLink } from "@/components/molecules/image-preview-link";
import { RequestState } from "@/components/molecules/request-state";
import { RelatedRecords } from "./related-records";
import { RecordHistory } from "./record-history";
import { JobLogs } from "./job-logs";
import { getRecord, jobKinds, type RecordData } from "@/lib/resource-api";
import { type Resource, resources, humanize } from "@/lib/resources";
import {
  recordIdentityKey,
  type RecordIdentity,
  type SimpleResource,
} from "@/lib/record-identities";
import { useRefreshInterval } from "@/lib/use-refresh-interval";
import { useSourcePipelineInterval } from "@/lib/use-source-pipeline-interval";
import { useRequest } from "@/lib/use-request";
import { adminArticleContent } from "@/lib/api/generated/admin";
import { imagePreviewUrl } from "@/lib/image-preview";
import { languageName } from "@/lib/languages";
import { StatusBadge } from "@/components/molecules/status-badge";
import { SourceRelevanceAssessment } from "./source-relevance-assessment";
import { SourcePublicationPolicy } from "./source-publication-policy";
import {
  hasUserAnalysisData,
  UserAnalysis,
  UserAnalysisAction,
  UserDetailsOverview,
  UserRecords,
} from "./user-details";
import type { AdminUserDetail } from "@/lib/api/generated/models";
import { AutomationHistory } from "./automation-history";
import { SourceReadiness } from "./source-readiness";
import { ArticleReadiness } from "./article-readiness";

export function ResourceDetail({
  identity,
  section = "details",
}: {
  identity: RecordIdentity;
  section?: DetailSection;
}) {
  const refreshSeconds = useRefreshInterval();
  const sourceSeconds = useSourcePipelineInterval();
  const [revision, setRevision] = useState(0);
  const load = useCallback((signal: AbortSignal) => getRecord(identity, signal), [identity]);
  const result = useRequest(
    recordIdentityKey(identity),
    load,
    (identity.resource === "sources" ? sourceSeconds : refreshSeconds) * 1000,
    revision,
  );
  return (
    <>
      <RequestState loading={result.loading} error={result.error} />
      {result.data && (
        <Details
          identity={identity}
          record={result.data}
          tab={section}
          onAnalysisQueued={() => setRevision((value) => value + 1)}
        />
      )}
    </>
  );
}
function Details({
  identity,
  record,
  tab,
  onAnalysisQueued,
}: {
  identity: RecordIdentity;
  record: RecordData;
  tab: DetailSection;
  onAnalysisQueued: () => void;
}) {
  const resource = identity.resource;
  const spec = resources[resource];
  const kind = identity.resource === "analysis-jobs" ? identity.kind : jobKinds[resource];
  const title =
    identity.resource === "topic-relations"
      ? humanize(identity.relation)
      : kind
        ? `Run ${identity.id.slice(0, 8)}`
        : String(record[spec.title]);
  const tabs = detailSections(resource);
  const logoField = spec.fields.find((field) => field.type === "logo-url");
  const logo =
    resource === "topics"
      ? imagePreviewUrl(record.managed_logo_url)
      : logoField
        ? imagePreviewUrl(record[logoField.key])
        : null;
  const fields = spec.fields
    .filter((field) => field.type !== "logo-url")
    .map((field) => {
      const rawValue = record[field.key];
      const value =
        field.type === "language" && typeof rawValue === "string" && rawValue
          ? languageName(rawValue)
          : rawValue;
      return {
        label: field.label,
        value:
          resource === "topics" && field.key === "description" ? (
            <p className="whitespace-pre-wrap break-words text-sm">{String(value ?? "")}</p>
          ) : typeof value === "string" &&
            /(?:description|summary|explanation|review_note)$/.test(field.key) ? (
            <Markdown>{value}</Markdown>
          ) : field.type === "image-url" ? (
            <ImagePreviewLink value={value} />
          ) : field.key.endsWith("status") || field.type === "boolean" ? (
            <StatusBadge value={value} />
          ) : field.type === "datetime" && value ? (
            <DateTime value={String(value)} />
          ) : field.type === "reference" && value ? (
            <RecordLink resource={field.resource!} id={String(value)} />
          ) : (
            <DataValue value={value} />
          ),
      };
    });
  if (resource === "articles")
    fields.push({
      label: "Sources",
      value: <LinkedItems resource="sources" items={record.sources} />,
    });
  const meta = [
    "id",
    "status",
    "approval_status",
    "review_status",
    "publication_status",
    "editorial_revision",
    "created_at",
    "updated_at",
    "last_seen_at",
    "discovered_at",
    "published_to_feed_at",
    "last_attempt_at",
    "last_success_at",
    "next_fetch_at",
    "adaptive_polling_enabled",
    "effective_polling_mode",
    "effective_interval_seconds",
    "polling_state",
    "consecutive_failures",
    "last_error",
    "reviewed_by",
    "reviewed_at",
    "review_note",
    "submitted_by",
    "submission_channel",
    "metadata_error",
    "metadata_enriched_at",
    "attempts",
    "available_at",
    "finished_at",
    "error",
  ].filter((key) => key in record && (resource !== "topic-relations" || key !== "id"));
  return (
    <section className="space-y-6">
      <ResourceHeading
        browserTitle={adminRouteTitle(
          {
            view: "detail",
            identity,
            section: tab,
          },
          identity.resource === "topic-relations" ? title : record[spec.title],
        )}
        title={title}
        leading={logo ? <ImagePreviewLink value={logo} kind="logo" variant="heading" /> : undefined}
        trail={[...resourceTrail(resource), { label: spec.label, href: resourceHref(resource) }]}
        description={spec.readonly ? spec.description : undefined}
      >
        {resource === "topics" && record.status === "active" && (
          <Button size="sm" variant="outline" asChild>
            <Link
              href={`/taxonomy/relationships/discover?topic_id=${encodeURIComponent(record.id)}`}
            >
              <Sparkles aria-hidden />
              Discover relationships
            </Link>
          </Button>
        )}
        {resource === "users" && (
          <UserAnalysisAction
            key={record.id}
            id={record.id}
            disabled={!hasUserAnalysisData(record as unknown as AdminUserDetail)}
            onQueued={onAnalysisQueued}
          />
        )}
        <RecordActions identity={identity} detail />
      </ResourceHeading>
      <nav aria-label="Object sections" className="flex gap-5 overflow-x-auto border-b">
        {tabs.map((item) => (
          <Link
            prefetch={false}
            key={item}
            href={recordHref(identity, item)}
            aria-current={tab === item ? "page" : undefined}
            className={`whitespace-nowrap border-b-2 px-1 pb-3 text-sm ${tab === item ? "border-primary font-medium" : "border-transparent text-muted-foreground hover:text-foreground"}`}
          >
            {item === "related"
              ? "Related objects"
              : item === "reading-days"
                ? "Reading days"
                : humanize(item)}
          </Link>
        ))}
      </nav>
      {tab === "details" && resource === "users" && (
        <UserDetailsOverview user={record as unknown as AdminUserDetail} />
      )}
      {tab === "details" && resource === "source-jobs" && record.details != null && (
        <ResourcePanel
          title="Source processing result"
          fields={[
            {
              label: "Operation",
              value: <DataValue value={(record.details as Record<string, unknown>).operation} />,
            },
            {
              label: "Result",
              value: <DataValue value={(record.details as Record<string, unknown>).result} />,
            },
          ]}
        />
      )}
      {tab === "details" && resource !== "users" && (
        <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
          <div className="space-y-6">
            {resource === "sources" && (
              <SourceReadiness id={identity.id} onUpdated={onAnalysisQueued} />
            )}
            {resource === "articles" && <ArticleReadiness id={record.id} />}
            {!!fields.length && <ResourcePanel title={spec.singular} fields={fields} />}
            {resource === "tags" && (
              <ResourcePanel
                title="Topic discovery"
                fields={[
                  { label: "Status", value: <DataValue value={record.topic_match_status} /> },
                  {
                    label: "Last checked",
                    value: record.topic_match_checked_at ? (
                      <DateTime value={String(record.topic_match_checked_at)} />
                    ) : (
                      <span>Waiting to start</span>
                    ),
                  },
                ]}
              />
            )}
            {resource === "topics" && (
              <ResourcePanel
                title="Logo"
                fields={[
                  { label: "Source", value: <DataValue value={record.logo_url} /> },
                  {
                    label: "Processing",
                    value: <StatusBadge value={record.logo_storage_status} />,
                  },
                  { label: "Error", value: <DataValue value={record.logo_storage_error} /> },
                ]}
              />
            )}
            {resource === "topics" && (
              <ResourcePanel title="Sourced facts">
                <DataValue value={record.facts} />
              </ResourcePanel>
            )}
            {resource === "articles" && (
              <>
                <ResourcePanel
                  title="Classification"
                  fields={[
                    {
                      label: "Topics",
                      value: <LinkedItems resource="topics" items={record.topics} />,
                    },
                    { label: "Tags", value: <LinkedItems resource="tags" items={record.tags} /> },
                  ]}
                />
              </>
            )}
            {kind && (
              <ResourcePanel title="Run details">
                {kind === "images" && (
                  <p className="mb-4">Operation: {imageJobOperation(record)}</p>
                )}
                <DataValue value={record.details} />
                {kind === "images" && record.product_id ? (
                  <p className="mt-4">
                    Product: <ImageJobSubject record={record} />
                  </p>
                ) : null}
                {record.source_id ? (
                  <p className="mt-4">
                    Source: <RecordLink resource="sources" id={String(record.source_id)} />
                  </p>
                ) : null}
                {record.article_id ? (
                  <p className="mt-4">
                    Article: <RecordLink resource="articles" id={String(record.article_id)} />
                  </p>
                ) : null}
                {record.topic_id ? (
                  <p className="mt-4">
                    Topic: <RecordLink resource="topics" id={String(record.topic_id)} /> ·{" "}
                    <Link
                      className="text-primary hover:underline"
                      href={`/taxonomy/relationships/proposals?job_id=${encodeURIComponent(record.id)}`}
                    >
                      Review relationship proposals
                    </Link>
                  </p>
                ) : null}
                {record.proposal_id ? (
                  <p className="mt-4">
                    <Link
                      className="text-primary hover:underline"
                      href={`/taxonomy/topics/proposals/${encodeURIComponent(String(record.proposal_id))}`}
                    >
                      Review topic proposal
                    </Link>
                  </p>
                ) : null}
              </ResourcePanel>
            )}
          </div>
          <div className="space-y-6">
            <ResourcePanel
              title="Record information"
              fields={meta.map((key) => ({
                label: humanize(key),
                value: key.endsWith("status") ? (
                  <StatusBadge value={record[key]} />
                ) : key.endsWith("_at") && record[key] ? (
                  <DateTime value={String(record[key])} />
                ) : (
                  <DataValue value={record[key]} />
                ),
              }))}
            />
            {(resource === "articles" || resource === "topics") && (
              <ResourcePanel title="AI-generated metadata">
                <p className="mb-4 text-xs text-muted-foreground">
                  Generated by AI. Kept separate from original and human-authored text.
                </p>
                <DataValue
                  value={
                    resource === "articles"
                      ? {
                          ...(typeof record.ai_title === "string" && record.ai_title.trim()
                            ? { ai_title: record.ai_title }
                            : {}),
                          ai_summary: record.ai_summary,
                          ai_description: record.ai_description,
                        }
                      : { ai_description: record.ai_description }
                  }
                />
              </ResourcePanel>
            )}
            {resource === "articles" && (
              <ResourcePanel title="Classification provenance">
                <DataValue value={record.classification_provenance} />
              </ResourcePanel>
            )}
            {resource === "sources" && (
              <SourcePublicationPolicy
                key={`${record.id}/${record.publication_policy_revision}`}
                id={record.id}
                mode={(record.publication_policy as "manual" | "preview" | "auto") ?? "manual"}
                revision={Number(record.publication_policy_revision ?? 0)}
                approved={record.approval_status === "approved"}
                fullAutomation={record.full_automation === true}
              />
            )}
          </div>
        </div>
      )}
      {resource === "sources" && tab === "relevance" && (
        <SourceRelevanceAssessment
          key={record.id}
          assessment={record.relevance_assessment}
          approvalStatus={record.approval_status}
        />
      )}
      {resource === "users" && tab === "analysis" && (
        <UserAnalysis user={record as unknown as AdminUserDetail} />
      )}
      {resource === "users" && tab !== "details" && tab !== "analysis" && (
        <UserRecords user={record as unknown as AdminUserDetail} section={tab as UserSection} />
      )}
      {tab === "related" && <Related resource={resource} record={record} />}
      {tab === "history" && (resource === "articles" || resource === "sources") && (
        <>
          <RecordHistory resource={resource} id={record.id} />
          <AutomationHistory resource={resource} id={record.id} />
        </>
      )}
      {tab === "evidence" && resource === "articles" && <Evidence id={record.id} />}
      {kind && identity.resource !== "topic-relations" && (tab === "details" || tab === "logs") && (
        <JobLogs kind={kind} id={identity.id} />
      )}
    </section>
  );
}
function LinkedItems({ resource, items }: { resource: SimpleResource; items: unknown }) {
  const rows = (items ?? []) as Record<string, unknown>[];
  return rows.length ? (
    <ul className="space-y-2">
      {rows.map((item) => (
        <li key={String(item.id ?? item.topic_id)}>
          <RecordLink
            resource={resource}
            id={String(item.id ?? item.topic_id)}
            label={String(item.name)}
          />
          {item.role ? (
            <span className="ml-2 text-xs text-muted-foreground">
              {humanize(String(item.role))} · {Math.round(Number(item.relevance) * 100)}%
            </span>
          ) : null}
          {item.evidence ? (
            <p className="mt-1 text-xs text-muted-foreground">Evidence: {String(item.evidence)}</p>
          ) : null}
        </li>
      ))}
    </ul>
  ) : (
    <span className="text-muted-foreground">None</span>
  );
}
function Related({ resource, record }: { resource: Resource; record: RecordData }) {
  const id = record.id;
  return (
    <div className="space-y-8">
      {resource === "sources" && (
        <>
          <RelatedRecords resource="articles" filter={{ source_id: id }} />
          <RelatedRecords resource="ingestion-jobs" filter={{ source_id: id }} />
          <RelatedRecords resource="source-jobs" filter={{ source_id: id }} />
        </>
      )}
      {resource === "topics" && (
        <>
          <RelatedRecords resource="analysis-jobs" filter={{ topic_id: id }} />
          <RelatedRecords resource="image-jobs" filter={{ topic_id: id }} />
          <RelatedRecords resource="articles" filter={{ topic_id: id }} />
          <RelatedRecords resource="topic-relations" filter={{ topic_id: id }} />
          <RelatedRecords resource="tags" filter={{ topic_id: id }} />
        </>
      )}
      {resource === "tags" && <RelatedRecords resource="articles" filter={{ tag_id: id }} />}
      {resource === "articles" && (
        <>
          <div className="grid gap-4 md:grid-cols-3">
            {(["sources", "tags"] as const).map((key) => (
              <ResourcePanel key={key} title={resources[key].label}>
                <LinkedItems resource={key} items={record[key]} />
              </ResourcePanel>
            ))}
          </div>
          <ResourcePanel title="Topics">
            <LinkedItems resource="topics" items={record.topics} />
          </ResourcePanel>
          <RelatedRecords resource="article-jobs" filter={{ article_id: id }} />
          <RelatedRecords resource="image-jobs" filter={{ article_id: id }} />
          <RelatedRecords resource="analysis-jobs" filter={{ article_id: id }} />
        </>
      )}
      {resource === "topic-relations" && (
        <ResourcePanel
          title="Linked topics"
          fields={[
            {
              label: "From topic",
              value: <RecordLink resource="topics" id={String(record.topic_id)} />,
            },
            {
              label: "To topic",
              value: <RecordLink resource="topics" id={String(record.related_topic_id)} />,
            },
          ]}
        />
      )}
    </div>
  );
}
function Evidence({ id }: { id: string }) {
  const load = useCallback((signal: AbortSignal) => adminArticleContent(id, { signal }), [id]);
  const result = useRequest(id, load);
  return (
    <>
      <RequestState loading={result.loading} error={result.error} />
      {!result.loading && !result.error && (
        <ResourcePanel title="Original extraction evidence">
          {result.data ? (
            <>
              <p className="mb-4 text-xs text-muted-foreground">
                {result.data.method} · <DateTime value={result.data.retrieved_at} />
              </p>
              <DataValue value={result.data.url} />
              <pre className="mt-4 max-h-[65vh] overflow-auto whitespace-pre-wrap break-words font-sans text-sm">
                {result.data.text}
              </pre>
            </>
          ) : (
            <p className="text-sm text-muted-foreground">
              Article text is unavailable. See the Details tab for the original feed information.
            </p>
          )}
        </ResourcePanel>
      )}
    </>
  );
}
