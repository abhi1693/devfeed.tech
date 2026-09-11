"use client";
import { Markdown } from "@devfeed/ui/markdown";

import { DateTime } from "@/components/molecules/date-time";
import { resourceHref, detailSections, resourceTrail, type DetailSection, type UserSection } from "@/lib/routes";
import Link from "next/link";
import { Network, Sparkles } from "lucide-react";
import { Button } from "@/components/atoms/button";
import { useCallback, useState } from "react";
import { adminRouteTitle } from "@/lib/page-titles";
import { PageHeading } from "@/components/molecules/page-heading";
import { RecordActions } from "@/components/molecules/record-actions";
import { RecordLink } from "@/components/molecules/record-link";
import { InfoPanel, DataValue } from "@/components/molecules/info-panel";
import { ImagePreviewLink } from "@/components/molecules/image-preview-link";
import { RequestState } from "@/components/molecules/request-state";
import { RelatedRecords } from "./related-records";
import { RecordHistory } from "./record-history";
import { JobLogs } from "./job-logs";
import { getRecord, jobKinds, type RecordData } from "@/lib/resource-api";
import { type Resource, resources, humanize, recordHref } from "@/lib/resources";
import { useRefreshInterval } from "@/lib/use-refresh-interval";
import { useRequest } from "@/lib/use-request";
import { adminArticleContent } from "@/lib/api/generated/admin";
import { openGraphHref } from "@/lib/knowledge-graph";
import type { GraphNode } from "@/lib/api/generated/models";
import { imagePreviewUrl } from "@/lib/image-preview";
import { languageName } from "@/lib/languages";
import { StatusBadge } from "@/components/molecules/status-badge";
import { SourcePublicationPolicy } from "./source-publication-policy";
import { hasUserAnalysisData, UserActivitySummary, UserAnalysis, UserAnalysisAction, UserFeedStatus, UserRecords } from "./user-details";
import type { AdminUserDetail } from "@/lib/api/generated/models";
import { AutomationHistory } from "./automation-history";

export function ResourceDetail({ resource, id, section = "details" }: { resource: Resource; id: string; section?: DetailSection }) {
  const refreshSeconds = useRefreshInterval();
  const [revision, setRevision] = useState(0);
  const load = useCallback((signal: AbortSignal) => getRecord(resource, id, signal), [resource, id]);
  const result = useRequest(`${resource}/${id}/${revision}`, load, refreshSeconds * 1000);
  return <><RequestState loading={result.loading} error={result.error} />{result.data && <Details resource={resource} record={result.data} tab={section} onAnalysisQueued={() => setRevision(value => value + 1)} />}</>;
}
function Details({ resource, record, tab, onAnalysisQueued }: { resource: Resource; record: RecordData; tab: DetailSection; onAnalysisQueued: () => void }) {
  const spec = resources[resource];
  const kind = resource === "analysis-jobs" && record.kind === "topic-analysis" ? "topic-analysis" : jobKinds[resource];
  const tabs = detailSections(resource);
  const logoField = spec.fields.find(field => field.type === "logo-url");
  const logo = logoField ? imagePreviewUrl(record[logoField.key]) : null;
  const fields = spec.fields.filter(field => field.type !== "logo-url").map(field => {
    const rawValue = record[field.key];
    const value = field.type === "language" && typeof rawValue === "string" && rawValue ? languageName(rawValue) : rawValue;
    return { label: field.label, value: typeof value === "string" && /(?:description|summary|explanation|review_note)$/.test(field.key) ? <Markdown>{value}</Markdown> : field.type === "image-url"
      ? <ImagePreviewLink value={value} />
      : field.key.endsWith("status") || field.type === "boolean" ? <StatusBadge value={value} />
      : field.type === "datetime" && value ? <DateTime value={String(value)} />
      : field.type === "reference" && value ? <RecordLink resource={field.resource!} id={String(value)} /> : <DataValue value={value} /> };
  });
  if (resource === "articles") fields.push({ label: "Sources", value: <LinkedItems resource="sources" items={record.sources} /> });
  const meta = ["id", "status", "approval_status", "review_status", "publication_status", "editorial_revision", "created_at", "updated_at", "last_seen_at", "discovered_at", "published_to_feed_at", "last_attempt_at", "last_success_at", "next_fetch_at", "consecutive_failures", "last_error", "reviewed_by", "reviewed_at", "review_note", "submitted_by", "submission_channel", "relevance_assessment", "metadata_error", "metadata_enriched_at", "attempts", "available_at", "finished_at", "error"].filter(key => key in record);
  return <section className="space-y-6"><PageHeading browserTitle={adminRouteTitle({ view: "detail", resource, id: resource === "analysis-jobs" && record.kind === "topic-analysis" ? `topic-analysis~${record.id}` : record.id, section: tab }, record[spec.title])} title={kind ? `Run ${record.id.slice(0, 8)}` : String(record[spec.title])} leading={logo ? <ImagePreviewLink value={logo} kind="logo" variant="heading" /> : undefined} trail={[...resourceTrail(resource), { label: spec.label, href: resourceHref(resource) }]} description={spec.readonly ? spec.description : undefined}>
    {resource === "topics" && record.status === "active" && <Button size="sm" variant="outline" asChild><Link href={`/taxonomy/relationships/discover?topic_id=${encodeURIComponent(record.id)}`}><Sparkles aria-hidden />Discover relationships</Link></Button>}
    {resource === "users" && <UserAnalysisAction key={record.id} id={record.id} disabled={!hasUserAnalysisData(record as unknown as AdminUserDetail)} onQueued={onAnalysisQueued} />}
    {(["topics", "articles", "tags", "sources", "users"].includes(resource) && (resource !== "topics" || record.status === "active")) && <Button variant="outline" size="sm" asChild><Link href={openGraphHref(resource.slice(0, -1) as GraphNode["kind"], record.id)}><Network aria-hidden />Open in graph</Link></Button>}
    <RecordActions resource={resource} id={record.id} detail />
  </PageHeading>
  <nav aria-label="Object sections" className="flex gap-5 overflow-x-auto border-b">{tabs.map(item => <Link prefetch={false} key={item} href={recordHref(resource, record, item)} aria-current={tab === item ? "page" : undefined} className={`whitespace-nowrap border-b-2 px-1 pb-3 text-sm ${tab === item ? "border-primary font-medium" : "border-transparent text-muted-foreground hover:text-foreground"}`}>{item === "related" ? "Related objects" : humanize(item)}</Link>)}</nav>
  {tab === "details" && <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]"><div className="space-y-6">{!!fields.length && <InfoPanel title={spec.singular} fields={fields} />}
    {resource === "users" && <UserActivitySummary user={record as unknown as AdminUserDetail} />}
    {resource === "tags" && <InfoPanel title="Topic discovery" fields={[{ label: "Status", value: <DataValue value={record.topic_match_status} /> }, { label: "Last checked", value: record.topic_match_checked_at ? <DateTime value={String(record.topic_match_checked_at)} /> : <span>Waiting for the next scheduler pass</span> }]} />}
    {resource === "topics" && <InfoPanel title="Sourced facts"><DataValue value={record.facts} /></InfoPanel>}
    {resource === "articles" && <><InfoPanel title="Classification" fields={[{ label: "Topics", value: <LinkedItems resource="topics" items={record.topics} /> }, { label: "Tags", value: <LinkedItems resource="tags" items={record.tags} /> }]} /><InfoPanel title="Publication requirements"><ul className="list-inside list-disc space-y-1 text-sm">{(record.publication_blockers as string[]).length ? (record.publication_blockers as string[]).map(reason => <li key={reason}>{humanize(reason)}</li>) : <li>All publication requirements are met.</li>}</ul></InfoPanel></>}
    {kind && <InfoPanel title="Run details"><DataValue value={record.details} />{record.source_id ? <p className="mt-4">Source: <RecordLink resource="sources" id={String(record.source_id)} /></p> : null}{record.article_id ? <p className="mt-4">Article: <RecordLink resource="articles" id={String(record.article_id)} /></p> : null}{record.topic_id ? <p className="mt-4">Topic: <RecordLink resource="topics" id={String(record.topic_id)} /> · <Link className="text-primary hover:underline" href={`/taxonomy/relationships/proposals?job_id=${encodeURIComponent(record.id)}`}>Review relationship proposals</Link></p> : null}{record.proposal_id ? <p className="mt-4"><Link className="text-primary hover:underline" href={`/taxonomy/topics/proposals/${encodeURIComponent(String(record.proposal_id))}`}>Review topic proposal</Link></p> : null}</InfoPanel>}
    </div><div className="space-y-6"><InfoPanel title="Record information" fields={meta.map(key => ({ label: humanize(key), value: key.endsWith("status") ? <StatusBadge value={record[key]} /> : key.endsWith("_at") && record[key] ? <DateTime value={String(record[key])} /> : <DataValue value={record[key]} /> }))} />
      {resource === "users" && <UserFeedStatus user={record as unknown as AdminUserDetail} />}
      {(resource === "articles" || resource === "topics") && <InfoPanel title="AI-generated metadata"><p className="mb-4 text-xs text-muted-foreground">Generated by AI. Kept separate from original and human-authored text.</p><DataValue value={resource === "articles" ? { ai_summary: record.ai_summary, ai_description: record.ai_description } : { ai_description: record.ai_description }} /></InfoPanel>}
      {resource === "articles" && <InfoPanel title="Classification provenance"><DataValue value={record.classification_provenance} /></InfoPanel>}
      {resource === "sources" && <SourcePublicationPolicy key={`${record.id}/${record.publication_policy_revision}`} id={record.id} mode={(record.publication_policy as "manual" | "preview" | "auto") ?? "manual"} revision={Number(record.publication_policy_revision ?? 0)} approved={record.approval_status === "approved"} fullAutomation={record.full_automation === true} />}
    </div></div>}
  {resource === "users" && tab === "analysis" && <UserAnalysis user={record as unknown as AdminUserDetail} />}
  {resource === "users" && tab !== "details" && tab !== "analysis" && <UserRecords user={record as unknown as AdminUserDetail} section={tab as UserSection} />}
  {tab === "related" && <Related resource={resource} record={record} />}
  {tab === "history" && (resource === "articles" || resource === "sources") && <><RecordHistory resource={resource} id={record.id} /><AutomationHistory resource={resource} id={record.id} /></>}
  {tab === "evidence" && resource === "articles" && <Evidence id={record.id} />}
  {kind && (tab === "details" || tab === "logs") && <JobLogs kind={kind} id={record.id} />}
  </section>;
}
function LinkedItems({ resource, items }: { resource: Resource; items: unknown }) {
  const rows = (items ?? []) as Record<string, unknown>[];
  return rows.length ? <ul className="space-y-2">{rows.map(item => <li key={String(item.id ?? item.topic_id)}><RecordLink resource={resource} id={String(item.id ?? item.topic_id)} label={String(item.name)} />{item.role ? <span className="ml-2 text-xs text-muted-foreground">{humanize(String(item.role))} · {Math.round(Number(item.relevance) * 100)}%</span> : null}{item.evidence ? <p className="mt-1 text-xs text-muted-foreground">Evidence: {String(item.evidence)}</p> : null}</li>)}</ul> : <span className="text-muted-foreground">None</span>;
}
function Related({ resource, record }: { resource: Resource; record: RecordData }) {
  const id = record.id;
  return <div className="space-y-8">
    {resource === "sources" && <><RelatedRecords resource="articles" filter={{ source_id: id }} /><RelatedRecords resource="ingestion-jobs" filter={{ source_id: id }} /><RelatedRecords resource="source-jobs" filter={{ source_id: id }} /></>}
    {resource === "topics" && <><RelatedRecords resource="analysis-jobs" filter={{ topic_id: id }} /><RelatedRecords resource="articles" filter={{ topic_id: id }} /><RelatedRecords resource="topic-relations" filter={{ topic_id: id }} /><RelatedRecords resource="tags" filter={{ topic_id: id }} /></>}
    {resource === "tags" && <RelatedRecords resource="articles" filter={{ tag_id: id }} />}
    {resource === "articles" && <><div className="grid gap-4 md:grid-cols-3">{(["sources", "tags"] as const).map(key => <InfoPanel key={key} title={resources[key].label}><LinkedItems resource={key} items={record[key]} /></InfoPanel>)}</div><InfoPanel title="Topics"><LinkedItems resource="topics" items={record.topics} /></InfoPanel><RelatedRecords resource="article-jobs" filter={{ article_id: id }} /><RelatedRecords resource="image-jobs" filter={{ article_id: id }} /><RelatedRecords resource="analysis-jobs" filter={{ article_id: id }} /></>}
    {resource === "topic-relations" && <InfoPanel title="Linked topics" fields={[{ label: "From topic", value: <RecordLink resource="topics" id={String(record.topic_id)} /> }, { label: "To topic", value: <RecordLink resource="topics" id={String(record.related_topic_id)} /> }]} />}
  </div>;
}
function Evidence({ id }: { id: string }) {
  const load = useCallback((signal: AbortSignal) => adminArticleContent(id, { signal }), [id]);
  const result = useRequest(id, load);
  return <><RequestState loading={result.loading} error={result.error} />{!result.loading && !result.error && <InfoPanel title="Original extraction evidence">{result.data ? <><p className="mb-4 text-xs text-muted-foreground">{result.data.method} · <DateTime value={result.data.retrieved_at} /></p><DataValue value={result.data.url} /><pre className="mt-4 max-h-[65vh] overflow-auto whitespace-pre-wrap break-words font-sans text-sm">{result.data.text}</pre></> : <p className="text-sm text-muted-foreground">No page extraction has been stored. Original feed metadata is available on the Details tab.</p>}</InfoPanel>}</>;
}
