"use client";
import Link from "next/link";
import { useCallback } from "react";
import { useSearchParams } from "next/navigation";
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
import { type Resource, resources, humanize } from "@/lib/resources";
import { useRequest } from "@/lib/use-request";
import { adminArticleContent } from "@/lib/api/generated/admin";
import { imagePreviewUrl } from "@/lib/image-preview";
import { languageName } from "@/lib/languages";
import { StatusBadge } from "@/components/molecules/status-badge";

export function ResourceDetail({ resource, id }: { resource: Resource; id: string }) {
  const load = useCallback((signal: AbortSignal) => getRecord(resource, id, signal), [resource, id]);
  const result = useRequest(`${resource}/${id}`, load);
  return <><RequestState loading={result.loading} error={result.error} />{result.data && <Details resource={resource} record={result.data} />}</>;
}
function Details({ resource, record }: { resource: Resource; record: RecordData }) {
  const spec = resources[resource]; const search = useSearchParams(); const tab = search.get("tab") || "details";
  const href = `/${resource}/${encodeURIComponent(record.id)}`;
  const kind = jobKinds[resource];
  const tabs = ["details", ...(kind ? ["logs"] : []), ...(!spec.readonly ? ["related"] : []), ...(["articles", "sources"].includes(resource) ? ["history"] : []), ...(resource === "articles" ? ["evidence"] : [])];
  const logoField = spec.fields.find(field => field.type === "logo-url");
  const logo = logoField ? imagePreviewUrl(record[logoField.key]) : null;
  const fields = spec.fields.filter(field => field.type !== "logo-url").map(field => {
    const rawValue = record[field.key];
    const value = field.type === "language" && typeof rawValue === "string" && rawValue ? languageName(rawValue) : rawValue;
    return { label: field.label, value: field.type === "image-url"
      ? <ImagePreviewLink value={value} />
      : field.key.endsWith("status") || field.type === "boolean" ? <StatusBadge value={value} />
      : field.type === "reference" && value ? <RecordLink resource={field.resource!} id={String(value)} /> : <DataValue value={value} /> };
  });
  if (resource === "articles") fields.push({ label: "Sources", value: <LinkedItems resource="sources" items={record.sources} /> });
  const meta = ["id", "status", "approval_status", "review_status", "publication_status", "editorial_revision", "created_at", "updated_at", "discovered_at", "published_to_feed_at", "last_attempt_at", "last_success_at", "next_fetch_at", "consecutive_failures", "last_error", "reviewed_by", "reviewed_at", "review_note", "submitted_by", "submission_channel", "metadata_error", "metadata_enriched_at", "attempts", "available_at", "finished_at", "error"].filter(key => key in record);
  return <section className="space-y-6"><PageHeading title={spec.readonly ? `Run ${record.id.slice(0, 8)}` : String(record[spec.title])} leading={logo ? <ImagePreviewLink value={logo} kind="logo" variant="heading" /> : undefined} trail={[{ label: spec.label, href: `/${resource}` }]} description={spec.readonly ? spec.description : undefined}>
    <RecordActions resource={resource} id={record.id} detail />
  </PageHeading>
  <nav aria-label="Object sections" className="flex gap-5 overflow-x-auto border-b">{tabs.map(item => <Link prefetch={false} key={item} href={`${href}?tab=${item}`} aria-current={tab === item ? "page" : undefined} className={`whitespace-nowrap border-b-2 px-1 pb-3 text-sm ${tab === item ? "border-primary font-medium" : "border-transparent text-muted-foreground hover:text-foreground"}`}>{item === "related" ? "Related objects" : humanize(item)}</Link>)}</nav>
  {tab === "details" && <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]"><div className="space-y-6">{!!fields.length && <InfoPanel title={spec.singular} fields={fields} />}
    {resource === "topics" && <InfoPanel title="Sourced facts"><DataValue value={record.facts} /></InfoPanel>}
    {resource === "articles" && <><InfoPanel title="Classification" fields={[{ label: "Topics", value: <LinkedItems resource="topics" items={record.topics} /> }, { label: "Categories", value: <LinkedItems resource="categories" items={record.categories} /> }, { label: "Tags", value: <LinkedItems resource="tags" items={record.tags} /> }]} /><InfoPanel title="Publication requirements"><ul className="list-inside list-disc space-y-1 text-sm">{(record.publication_blockers as string[]).length ? (record.publication_blockers as string[]).map(reason => <li key={reason}>{humanize(reason)}</li>) : <li>All publication requirements are met.</li>}</ul></InfoPanel></>}
    {spec.readonly && <InfoPanel title="Run details"><DataValue value={record.details} />{record.source_id ? <p className="mt-4">Source: <RecordLink resource="sources" id={String(record.source_id)} /></p> : null}{record.article_id ? <p className="mt-4">Article: <RecordLink resource="articles" id={String(record.article_id)} /></p> : null}</InfoPanel>}
    </div><div className="space-y-6"><InfoPanel title="Record information" fields={meta.map(key => ({ label: humanize(key), value: key.endsWith("status") ? <StatusBadge value={record[key]} /> : <DataValue value={record[key]} /> }))} />
      {(resource === "articles" || resource === "topics") && <InfoPanel title="AI-generated metadata"><p className="mb-4 text-xs text-muted-foreground">Generated by AI. Kept separate from original and human-authored text.</p><DataValue value={resource === "articles" ? { ai_summary: record.ai_summary, ai_description: record.ai_description } : { ai_description: record.ai_description }} /></InfoPanel>}
      {resource === "articles" && <InfoPanel title="Classification provenance"><DataValue value={record.classification_provenance} /></InfoPanel>}
    </div></div>}
  {tab === "related" && <Related resource={resource} record={record} />}
  {tab === "history" && (resource === "articles" || resource === "sources") && <RecordHistory resource={resource} id={record.id} />}
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
    {resource === "topics" && <><RelatedRecords resource="articles" filter={{ topic_id: id }} /><RelatedRecords resource="topic-relations" filter={{ topic_id: id }} /><RelatedRecords resource="categories" filter={{ topic_id: id }} /><RelatedRecords resource="tags" filter={{ topic_id: id }} /></>}
    {resource === "categories" && <><RelatedRecords resource="categories" title="Child categories" filter={{ parent_id: id }} /><RelatedRecords resource="tags" filter={{ category_id: id }} /><RelatedRecords resource="articles" filter={{ category_id: id }} /></>}
    {resource === "tags" && <RelatedRecords resource="articles" filter={{ tag_id: id }} />}
    {resource === "articles" && <><div className="grid gap-4 md:grid-cols-3">{(["sources", "categories", "tags"] as const).map(key => <InfoPanel key={key} title={resources[key].label}><LinkedItems resource={key} items={record[key]} /></InfoPanel>)}</div><InfoPanel title="Topics"><LinkedItems resource="topics" items={record.topics} /></InfoPanel><RelatedRecords resource="article-jobs" filter={{ article_id: id }} /><RelatedRecords resource="image-jobs" filter={{ article_id: id }} /><RelatedRecords resource="analysis-jobs" filter={{ article_id: id }} /></>}
    {resource === "topic-relations" && <InfoPanel title="Linked topics" fields={[{ label: "From topic", value: <RecordLink resource="topics" id={String(record.topic_id)} /> }, { label: "To topic", value: <RecordLink resource="topics" id={String(record.related_topic_id)} /> }]} />}
  </div>;
}
function Evidence({ id }: { id: string }) {
  const load = useCallback((signal: AbortSignal) => adminArticleContent(id, { signal }), [id]);
  const result = useRequest(id, load);
  return <><RequestState loading={result.loading} error={result.error} />{!result.loading && !result.error && <InfoPanel title="Original extraction evidence">{result.data ? <><p className="mb-4 text-xs text-muted-foreground">{result.data.method} · {new Date(result.data.retrieved_at).toLocaleString()}</p><DataValue value={result.data.url} /><pre className="mt-4 max-h-[65vh] overflow-auto whitespace-pre-wrap break-words font-sans text-sm">{result.data.text}</pre></> : <p className="text-sm text-muted-foreground">No page extraction has been stored. Original feed metadata is available on the Details tab.</p>}</InfoPanel>}</>;
}
