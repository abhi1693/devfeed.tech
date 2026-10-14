/** A single typed adapter over Orval. No handwritten HTTP endpoints in screens. */
import * as api from "@/lib/api/generated/admin";
import type {
  AdminArticleCreate,
  AdminArticleUpdate,
  AdminTopicWrite,
  RelationOut,
  RelationshipOut,
  RelationWrite,
  AdminSourceCreate,
  SourcePatch,
  TagWrite,
  AdminJobOut,
} from "@/lib/api/generated/models";
import { type Resource } from "./resources";
import { recordRowKey, type RecordIdentity } from "./record-identities";

export type RecordData = Record<string, unknown> & { id: string };
export type RecordPage = { items: RecordData[]; total: number; offset: number; limit: number };
export type ListParams = {
  q?: string;
  sort?: string;
  offset?: number;
  limit?: number;
  [key: string]: string | number | undefined;
};
export const jobKinds: Partial<Record<Resource, AdminJobOut["kind"]>> = {
  "ingestion-jobs": "ingestion",
  "article-jobs": "article-enrichment",
  "image-jobs": "images",
  "source-jobs": "source-enrichment",
  "analysis-jobs": "analysis",
  "notification-jobs": "notifications",
};
export function retryRecordJob(resource: Resource, row: RecordData, csrf: string) {
  const kind = (row.kind as AdminJobOut["kind"] | undefined) ?? jobKinds[resource];
  if (!kind) throw new Error("This record is not a job");
  return api.adminJobRetry(kind, row.id, { headers: { "X-CSRF-Token": csrf } });
}
export function asRecord(value: unknown, resource: Resource): RecordData {
  if (resource === "topic-relations") {
    const relationship = value as RelationOut | RelationshipOut;
    return {
      ...relationship,
      id: recordRowKey(resource, relationship),
    };
  }
  return value as RecordData;
}
export async function listRecords(
  resource: Resource,
  params: ListParams = {},
  signal?: AbortSignal,
): Promise<RecordPage> {
  const options = { signal };
  let page;
  switch (resource) {
    case "users":
      page = await api.adminUsersList(params, options);
      break;
    case "articles":
      page = await api.adminArticlesList(params, options);
      break;
    case "sources":
      page = await api.adminSourcesList(params, options);
      break;
    case "topics":
      page = await api.adminTopicsList(params, options);
      break;
    case "tags":
      page = await api.adminTagsList(params, options);
      break;
    case "topic-relations":
      page = await api.adminRelationshipsList(params, options);
      break;
    case "analysis-jobs":
      page = await api.adminAiAnalysisJobsList(params, options);
      break;
    default:
      page = await api.adminJobsList(jobKinds[resource]!, params, options);
  }
  return { ...page, items: page.items.map((value) => asRecord(value, resource)) };
}
export async function getRecord(
  identity: RecordIdentity,
  signal?: AbortSignal,
): Promise<RecordData> {
  const options = { signal };
  let value;
  switch (identity.resource) {
    case "users":
      value = await api.adminUserGet(identity.id, options);
      break;
    case "articles":
      value = await api.adminArticleGet(identity.id, options);
      break;
    case "sources":
      value = await api.adminSourceGet(identity.id, options);
      break;
    case "topics":
      value = await api.adminTopicGet(identity.id, options);
      break;
    case "tags":
      value = await api.adminTagGet(identity.id, options);
      break;
    case "topic-relations":
      value = await api.adminRelationGet(
        identity.topicId,
        identity.relatedTopicId,
        identity.relation,
        options,
      );
      break;
    case "analysis-jobs":
      value = await api.adminJobGet(identity.kind, identity.id, options);
      break;
    default:
      value = await api.adminJobGet(jobKinds[identity.resource]!, identity.id, options);
  }
  return asRecord(value, identity.resource);
}
export async function saveRecord(
  resource: Resource,
  body: Record<string, unknown>,
  csrf: string,
  identity?: RecordIdentity,
): Promise<RecordData> {
  if (identity && identity.resource !== resource)
    throw new Error("Record identity does not match the resource being saved");
  const options = { headers: { "X-CSRF-Token": csrf } };
  const id = identity && "id" in identity ? identity.id : undefined;
  let value;
  switch (resource) {
    case "articles":
      value = id
        ? await api.adminArticleUpdate(id, body as unknown as AdminArticleUpdate, options)
        : await api.adminArticleCreate(body as unknown as AdminArticleCreate, options);
      break;
    case "sources":
      value = id
        ? await api.adminSourceUpdate(id, body as SourcePatch, options)
        : await api.adminSourceCreate(body as unknown as AdminSourceCreate, options);
      break;
    case "topics":
      value = id
        ? await api.adminTopicUpdate(id, body as unknown as AdminTopicWrite, options)
        : await api.adminTopicCreate(body as unknown as AdminTopicWrite, options);
      break;
    case "tags":
      value = id
        ? await api.adminTagUpdate(id, body as unknown as TagWrite, options)
        : await api.adminTagCreate(body as unknown as TagWrite, options);
      break;
    case "topic-relations":
      value =
        identity?.resource === "topic-relations"
          ? await api.adminRelationUpdate(
              identity.topicId,
              identity.relatedTopicId,
              identity.relation,
              body as unknown as RelationWrite,
              options,
            )
          : await api.adminRelationCreate(body as unknown as RelationWrite, options);
      break;
    default:
      throw new Error("Worker records are read-only");
  }
  return asRecord(value, resource);
}
export async function deleteRecord(
  identity: RecordIdentity,
  csrf: string,
  replacementTopicId?: string,
) {
  const options = { headers: { "X-CSRF-Token": csrf } };
  switch (identity.resource) {
    case "articles":
      return api.adminArticleDelete(identity.id, options);
    case "sources":
      return api.adminSourceDelete(identity.id, options);
    case "topics":
      return api.adminTopicDelete(
        identity.id,
        replacementTopicId?.startsWith("proposal:")
          ? { replacement_proposal_id: replacementTopicId.slice("proposal:".length) }
          : { replacement_topic_id: replacementTopicId },
        options,
      );
    case "tags":
      return api.adminTagDelete(identity.id, options);
    case "topic-relations":
      return api.adminRelationDelete(
        identity.topicId,
        identity.relatedTopicId,
        identity.relation,
        options,
      );
    default:
      throw new Error("Worker records are read-only");
  }
}

export async function listUserRecords(
  id: string,
  section: import("./routes").UserSection,
  params: ListParams,
  signal?: AbortSignal,
): Promise<RecordPage> {
  const options = { signal };
  let page;
  switch (section) {
    case "sources":
      page = await api.adminUserSources(id, params, options);
      break;
    case "topics":
      page = await api.adminUserTopics(id, params, options);
      break;
    case "likes":
      page = await api.adminUserLikes(id, params, options);
      break;
    case "bookmarks":
      page = await api.adminUserBookmarks(id, params, options);
      break;
    case "reads":
      page = await api.adminUserReads(id, params, options);
      break;
    case "reading-days":
      page = await api.adminUserReadingDays(id, params, options);
      break;
    case "interests":
      page = await api.adminUserInterests(id, params, options);
      break;
    case "recommendations":
      page = await api.adminUserRecommendations(id, params, options);
      break;
  }
  return { ...page, items: page.items.map((value) => asRecord(value, "users")) };
}
