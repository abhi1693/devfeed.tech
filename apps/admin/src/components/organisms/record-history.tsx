"use client";
import { useCallback, useState } from "react";
import { adminArticleReviews, adminSourceReviews } from "@/lib/api/generated/admin";
import { RequestState } from "@/components/molecules/request-state";
import { DataValue } from "@/components/molecules/info-panel";
import { Button } from "@/components/atoms/button";
import { useRequest } from "@/lib/use-request";
import type { PageArticleReviewOut, PageSourceReviewOut } from "@/lib/api/generated/models";
export function RecordHistory({ resource, id }: { resource: "articles" | "sources"; id: string }) {
  const [offset, setOffset] = useState(0);
  const load = useCallback((signal: AbortSignal) => resource === "articles" ? adminArticleReviews(id, { offset, limit: 20 }, { signal }) : adminSourceReviews(id, { offset, limit: 20 }, { signal }), [resource, id, offset]);
  const result = useRequest<PageArticleReviewOut | PageSourceReviewOut>(`${resource}/${id}/${offset}`, load);
  return <section className="space-y-4"><h2 className="font-semibold">Review history</h2><RequestState loading={result.loading} error={result.error} />{result.data && <><p className="text-xs text-muted-foreground">{result.data.total} decisions. History is read-only.</p><ol className="divide-y rounded-lg border bg-card px-5">{result.data.items.map(item => <li key={item.id} className="space-y-2 py-4 text-sm"><div className="flex flex-wrap justify-between gap-2"><strong className="capitalize">{"action" in item ? item.action : item.decision}</strong><time className="text-xs text-muted-foreground">{new Date(item.created_at).toLocaleString()}</time></div><p className="break-all text-xs text-muted-foreground">Actor: {item.actor ?? "Not recorded"}{"revision" in item ? ` · Revision ${item.revision}` : ""}</p>{item.note && <DataValue value={item.note} />}</li>)}</ol><div className="flex gap-2"><Button variant="outline" size="sm" disabled={!offset} onClick={() => setOffset(offset - 20)}>Previous</Button><Button variant="outline" size="sm" disabled={offset + 20 >= result.data.total} onClick={() => setOffset(offset + 20)}>Next</Button></div></>}</section>;
}
