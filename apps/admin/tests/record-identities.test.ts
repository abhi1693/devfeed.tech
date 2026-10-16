import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "@/lib/api/generated/admin";
import { asRecord, deleteRecord, getRecord, retryRecordJob, saveRecord } from "@/lib/resource-api";
import {
  recordIdentity,
  recordIdentityKey,
  recordRowKey,
  type AnalysisIdentity,
  type RelationshipIdentity,
} from "@/lib/record-identities";

vi.mock("@/lib/api/generated/admin", () => ({
  adminRelationGet: vi.fn(),
  adminRelationUpdate: vi.fn(),
  adminRelationDelete: vi.fn(),
  adminJobGet: vi.fn(),
  adminJobRetry: vi.fn(),
  adminTagUpdate: vi.fn(),
}));

const sharedId = "22222222-2222-4222-8222-222222222222";
const topicId = "11111111-1111-4111-8111-111111111111";
const relatedTopicId = "33333333-3333-4333-8333-333333333333";
const relationship = {
  topic_id: topicId,
  related_topic_id: relatedTopicId,
  relation: "depends_on" as const,
  evidence_url: "https://example.com/evidence",
};
const forward: RelationshipIdentity = {
  resource: "topic-relations",
  topicId,
  relatedTopicId,
  relation: "depends_on",
};
const reverse: RelationshipIdentity = {
  ...forward,
  topicId: relatedTopicId,
  relatedTopicId: topicId,
};
const options = { headers: { "X-CSRF-Token": "csrf" } };

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.adminRelationGet).mockResolvedValue(relationship);
  vi.mocked(api.adminRelationUpdate).mockResolvedValue(relationship);
  vi.mocked(api.adminRelationDelete).mockResolvedValue(undefined);
});

describe("native record identities", () => {
  it.each(["analysis", "topic-analysis"] as const)(
    "preserves the raw UUID and %s kind in detail requests and retries",
    async (kind) => {
      const identity: AnalysisIdentity = { resource: "analysis-jobs", kind, id: sharedId };
      const row = { id: sharedId, kind, status: "failed" };
      vi.mocked(api.adminJobGet).mockResolvedValue(
        row as Awaited<ReturnType<typeof api.adminJobGet>>,
      );
      const signal = new AbortController().signal;
      expect(await getRecord(identity, signal)).toEqual(row);
      expect(api.adminJobGet).toHaveBeenCalledWith(kind, sharedId, { signal });
      await retryRecordJob("analysis-jobs", row, "csrf");
      expect(api.adminJobRetry).toHaveBeenCalledWith(kind, sharedId, options);
    },
  );

  it("keeps colliding analysis UUIDs distinct without replacing either native ID", () => {
    const article = asRecord({ id: sharedId, kind: "analysis" }, "analysis-jobs");
    const topic = asRecord({ id: sharedId, kind: "topic-analysis" }, "analysis-jobs");
    expect(article.id).toBe(sharedId);
    expect(topic.id).toBe(sharedId);
    expect(recordRowKey("analysis-jobs", article)).not.toBe(recordRowKey("analysis-jobs", topic));
    expect(recordIdentity("analysis-jobs", topic)).toEqual({
      resource: "analysis-jobs",
      kind: "topic-analysis",
      id: sharedId,
    });
  });

  it("treats delimiter-looking IDs as opaque API values", async () => {
    const id = "topic-analysis~opaque";
    const identity: AnalysisIdentity = { resource: "analysis-jobs", kind: "analysis", id };
    vi.mocked(api.adminJobGet).mockResolvedValue({ id, kind: "analysis" } as Awaited<
      ReturnType<typeof api.adminJobGet>
    >);
    await getRecord(identity);
    expect(api.adminJobGet).toHaveBeenCalledWith("analysis", id, { signal: undefined });
  });

  it("preserves relationship API fields and uses separate display keys for proposals", () => {
    const approved = asRecord({ ...relationship, proposal: null }, "topic-relations");
    const pending = asRecord({ ...relationship, proposal: { id: sharedId } }, "topic-relations");
    expect(approved).toEqual({
      ...relationship,
      proposal: null,
      id: recordRowKey("topic-relations", relationship),
    });
    expect(approved.id).toBe(recordIdentityKey(forward));
    expect(recordIdentity("topic-relations", approved)).toEqual(forward);
    expect(recordRowKey("topic-relations", pending)).not.toBe(
      recordRowKey("topic-relations", approved),
    );
    expect(recordIdentityKey(forward)).not.toBe(recordIdentityKey(reverse));
  });

  it.each([forward, reverse])(
    "uses the exact relationship direction for GET, PUT and DELETE: $topicId → $relatedTopicId",
    async (identity) => {
      const signal = new AbortController().signal;
      const update = { ...relationship, evidence_url: "https://example.com/revised" };
      await getRecord(identity, signal);
      expect(api.adminRelationGet).toHaveBeenCalledWith(
        identity.topicId,
        identity.relatedTopicId,
        identity.relation,
        { signal },
      );
      await saveRecord("topic-relations", update, "csrf", identity);
      expect(api.adminRelationUpdate).toHaveBeenCalledWith(
        identity.topicId,
        identity.relatedTopicId,
        identity.relation,
        update,
        options,
      );
      await deleteRecord(identity, "csrf");
      expect(api.adminRelationDelete).toHaveBeenCalledWith(
        identity.topicId,
        identity.relatedTopicId,
        identity.relation,
        options,
      );
    },
  );

  it.each([
    { ...relationship, relation: "inherits_from" },
    { ...relationship, relation: "" },
    { ...relationship, topic_id: undefined },
    { ...relationship, related_topic_id: "" },
    { ...relationship, topic_id: null },
  ])("rejects malformed native relationship fields before any request: %j", (row) => {
    expect(() => recordIdentity("topic-relations", row)).toThrow();
    expect(api.adminRelationGet).not.toHaveBeenCalled();
    expect(api.adminRelationUpdate).not.toHaveBeenCalled();
    expect(api.adminRelationDelete).not.toHaveBeenCalled();
  });

  it.each([undefined, "", "ingestion", "topic-analysis~id"])(
    "rejects unsupported analysis kinds instead of falling back to article analysis: %s",
    (kind) => {
      expect(() => recordIdentity("analysis-jobs", { id: sharedId, kind })).toThrow();
      expect(api.adminJobGet).not.toHaveBeenCalled();
    },
  );

  it("rejects an edit identity for a different resource before saving", async () => {
    await expect(saveRecord("tags", { name: "Tag" }, "csrf", forward)).rejects.toThrow();
    expect(api.adminTagUpdate).not.toHaveBeenCalled();
    expect(api.adminRelationUpdate).not.toHaveBeenCalled();
  });
});
