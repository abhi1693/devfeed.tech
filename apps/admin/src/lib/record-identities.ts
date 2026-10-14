import { RelationOutRelation } from "@/lib/api/generated/models";
import type { Resource } from "./resources";

export type SimpleResource = Exclude<Resource, "analysis-jobs" | "topic-relations">;
export type SimpleIdentity = {
  [Key in SimpleResource]: { resource: Key; id: string };
}[SimpleResource];
export type AnalysisIdentity = {
  resource: "analysis-jobs";
  kind: "analysis" | "topic-analysis";
  id: string;
};
export type RelationshipIdentity = {
  resource: "topic-relations";
  topicId: string;
  relatedTopicId: string;
  relation: RelationOutRelation;
};
export type RecordIdentity = SimpleIdentity | AnalysisIdentity | RelationshipIdentity;

type IdentityFields = {
  id?: unknown;
  kind?: unknown;
  topic_id?: unknown;
  related_topic_id?: unknown;
  relation?: unknown;
  proposal?: { id: string } | null;
};

function identifier(value: unknown): string {
  if (typeof value !== "string" || !value) throw new Error("Missing record identifier");
  return value;
}

/** Native API fields become a typed identity without interpreting an ID's contents. */
export function recordIdentity(resource: Resource, record: IdentityFields): RecordIdentity {
  if (resource === "analysis-jobs") {
    if (record.kind !== "analysis" && record.kind !== "topic-analysis")
      throw new Error("Missing analysis job kind");
    return { resource, kind: record.kind, id: identifier(record.id) };
  }
  if (resource === "topic-relations") {
    const relation = Object.values(RelationOutRelation).find((value) => value === record.relation);
    if (!relation) throw new Error("Invalid relationship type");
    return {
      resource,
      topicId: identifier(record.topic_id),
      relatedTopicId: identifier(record.related_topic_id),
      relation,
    };
  }
  return { resource, id: identifier(record.id) };
}

/** Serialization is only a stable UI/request key; it is never decoded into an identity. */
export function recordIdentityKey(identity: RecordIdentity): string {
  switch (identity.resource) {
    case "analysis-jobs":
      return JSON.stringify([identity.resource, identity.kind, identity.id]);
    case "topic-relations":
      return JSON.stringify([
        identity.resource,
        identity.topicId,
        identity.relation,
        identity.relatedTopicId,
      ]);
    default:
      return JSON.stringify([identity.resource, identity.id]);
  }
}

export function recordRowKey(resource: Resource, record: IdentityFields): string {
  if (resource === "topic-relations" && record.proposal)
    return JSON.stringify([resource, "proposal", identifier(record.proposal.id)]);
  return recordIdentityKey(recordIdentity(resource, record));
}
