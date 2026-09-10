"use client";

import { useCallback, useState } from "react";
import { adminPublicationDecisions, adminPublicationPolicyHistory } from "@/lib/api/generated/admin";
import type { PagePublicationDecisionOut, PagePublicationPolicyReviewOut } from "@/lib/api/generated/models";
import { useRequest } from "@/lib/use-request";
import { Button } from "@/components/atoms/button";
import { DateTime } from "@/components/molecules/date-time";
import { DataValue } from "@/components/molecules/info-panel";
import { RequestState } from "@/components/molecules/request-state";

export function AutomationHistory({ resource, id }: { resource: "articles" | "sources"; id: string }) {
  const [offset, setOffset] = useState(0);
  const load = useCallback((signal: AbortSignal) => resource === "articles" ? adminPublicationDecisions(id, { offset, limit: 20 }, { signal }) : adminPublicationPolicyHistory(id, { offset, limit: 20 }, { signal }), [resource, id, offset]);
  const result = useRequest<PagePublicationDecisionOut | PagePublicationPolicyReviewOut>(`automation/${resource}/${id}/${offset}`, load);
  return <section className="space-y-4"><h2 className="font-semibold">{resource === "articles" ? "Publication policy decisions" : "Publication policy history"}</h2><RequestState loading={result.loading} error={result.error} />{result.data && <><p className="text-xs text-muted-foreground">{result.data.total} records. Preview decisions are retained after the policy changes.</p><ol className="divide-y rounded-lg border bg-card px-5">{result.data.items.map(item => <li key={item.id} className="space-y-2 py-4 text-sm"><DateTime value={item.created_at} />{"decision" in item ? <DataValue value={item.decision} /> : <p>Mode: {item.mode} · Revision {item.revision} · Actor: {item.actor}</p>}</li>)}</ol><div className="flex gap-2"><Button variant="outline" size="sm" disabled={!offset} onClick={() => setOffset(offset - 20)}>Previous decisions</Button><Button variant="outline" size="sm" disabled={offset + 20 >= result.data.total} onClick={() => setOffset(offset + 20)}>Next decisions</Button></div></>}</section>;
}
