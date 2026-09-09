/** A single typed adapter over Orval. No handwritten HTTP endpoints in screens. */
import * as api from "@/lib/api/generated/admin";
import type { AdminArticleCreate, AdminArticleUpdate, AdminTopicWrite, RelationOut, RelationshipOut, RelationWrite, SourceCreate, SourcePatch, TagWrite, AdminJobOut } from "@/lib/api/generated/models";
import { type Resource } from "./resources";

export type RecordData = Record<string, unknown> & { id: string };
export type RecordPage = { items: RecordData[]; total: number; offset: number; limit: number };
export type ListParams = { q?: string; sort?: string; offset?: number; limit?: number; [key: string]: string | number | undefined };
export const jobKinds: Partial<Record<Resource, AdminJobOut["kind"]>> = { "ingestion-jobs": "ingestion", "article-jobs": "article-enrichment", "image-jobs": "images", "source-jobs": "source-enrichment", "analysis-jobs": "analysis", "notification-jobs": "notifications" };
export function retryRecordJob(resource: Resource, row: RecordData, csrf: string) {
  const kind = row.kind as AdminJobOut["kind"] | undefined ?? jobKinds[resource];
  if (!kind) throw new Error("This record is not a job");
  return api.adminJobRetry(kind, row.id, { headers: { "X-CSRF-Token": csrf } });
}
function relationKey(value: RelationOut) { return `${value.topic_id}~${value.related_topic_id}~${value.relation}`; }
function relationParts(id: string): [string, string, RelationOut["relation"]] {
  const [from, to, kind, extra] = id.split("~");
  if (!from || !to || !kind || extra) throw new Error("Invalid relationship URL");
  return [from, to, kind as RelationOut["relation"]];
}
export function asRecord(value: unknown, resource: Resource): RecordData {
  if (resource === "topic-relations") {
    const relationship = value as RelationOut | RelationshipOut;
    return { ...relationship, id: "proposal" in relationship && relationship.proposal ? `proposal~${relationship.proposal.id}` : relationKey(relationship) };
  }
  return value as RecordData;
}
export async function listRecords(resource: Resource, params: ListParams = {}, signal?: AbortSignal): Promise<RecordPage> {
  const options = { signal };
  let page;
  switch (resource) {
    case "articles": page = await api.adminArticlesList(params, options); break;
    case "sources": page = await api.adminSourcesList(params, options); break;
    case "topics": page = await api.adminTopicsList(params, options); break;
    case "tags": page = await api.adminTagsList(params, options); break;
    case "topic-relations": page = await api.adminRelationshipsList(params, options); break;
    case "analysis-jobs": page = await api.adminAiAnalysisJobsList(params, options); break;
    default: page = await api.adminJobsList(jobKinds[resource]!, params, options);
  }
  return { ...page, items: page.items.map(value => asRecord(value, resource)) };
}
export async function getRecord(resource: Resource, id: string, signal?: AbortSignal): Promise<RecordData> {
  const options = { signal };
  let value;
  switch (resource) {
    case "articles": value = await api.adminArticleGet(id, options); break;
    case "sources": value = await api.adminSourceGet(id, options); break;
    case "topics": value = await api.adminTopicGet(id, options); break;
    case "tags": value = await api.adminTagGet(id, options); break;
    case "topic-relations": value = await api.adminRelationGet(...relationParts(id), options); break;
    case "analysis-jobs": value = id.startsWith("topic-analysis~")
      ? await api.adminJobGet("topic-analysis", id.slice("topic-analysis~".length), options)
      : await api.adminJobGet("analysis", id, options); break;
    default: value = await api.adminJobGet(jobKinds[resource]!, id, options);
  }
  return asRecord(value, resource);
}
export async function saveRecord(resource: Resource, body: Record<string, unknown>, csrf: string, id?: string): Promise<RecordData> {
  const options = { headers: { "X-CSRF-Token": csrf } };
  let value;
  switch (resource) {
    case "articles": value = id ? await api.adminArticleUpdate(id, body as unknown as AdminArticleUpdate, options) : await api.adminArticleCreate(body as unknown as AdminArticleCreate, options); break;
    case "sources": value = id ? await api.adminSourceUpdate(id, body as SourcePatch, options) : await api.adminSourceCreate(body as unknown as SourceCreate, options); break;
    case "topics": value = id ? await api.adminTopicUpdate(id, body as unknown as AdminTopicWrite, options) : await api.adminTopicCreate(body as unknown as AdminTopicWrite, options); break;
    case "tags": value = id ? await api.adminTagUpdate(id, body as unknown as TagWrite, options) : await api.adminTagCreate(body as unknown as TagWrite, options); break;
    case "topic-relations": value = id ? await api.adminRelationUpdate(...relationParts(id), body as unknown as RelationWrite, options) : await api.adminRelationCreate(body as unknown as RelationWrite, options); break;
    default: throw new Error("Worker records are read-only");
  }
  return asRecord(value, resource);
}
export async function deleteRecord(resource: Resource, id: string, csrf: string, replacementTopicId?: string) {
  const options = { headers: { "X-CSRF-Token": csrf } };
  switch (resource) {
    case "articles": return api.adminArticleDelete(id, options);
    case "sources": return api.adminSourceDelete(id, options);
    case "topics": return api.adminTopicDelete(id, replacementTopicId?.startsWith("proposal:") ? { replacement_proposal_id: replacementTopicId.slice("proposal:".length) } : { replacement_topic_id: replacementTopicId }, options);
    case "tags": return api.adminTagDelete(id, options);
    case "topic-relations": return api.adminRelationDelete(...relationParts(id), options);
    default: throw new Error("Worker records are read-only");
  }
}
