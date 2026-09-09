"use client";

import { resourceTrail } from "@/lib/routes";
import Link from "next/link";
import { useCallback, useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/atoms/button";
import { Input } from "@/components/atoms/input";
import { PageHeading } from "@/components/molecules/page-heading";
import { RequestState } from "@/components/molecules/request-state";
import { useAdmin } from "@/components/molecules/admin-session";
import { adminTopicEnrichmentPreview, adminTopicEnrichmentSubmit } from "@/lib/api/generated/admin";
import { useRequest } from "@/lib/use-request";
import { notify, notifyFailure } from "@/lib/notifications";

export function TopicEnrichment({ id }: { id: string }) {
  const admin = useAdmin(); const router = useRouter();
  const [revision, setRevision] = useState(0); const [selected, setSelected] = useState<string[]>([]);
  const [busy, setBusy] = useState(false); const [error, setError] = useState<Error>();
  const load = useCallback((signal: AbortSignal) => adminTopicEnrichmentPreview(id, { signal, headers: { "X-CSRF-Token": admin.csrf_token } }), [id, admin.csrf_token]);
  const result = useRequest(`${id}/${revision}`, load);
  async function submit() {
    if (!result.data || !selected.length || busy) return;
    setBusy(true); setError(undefined);
    try {
      const proposal = await adminTopicEnrichmentSubmit(id, { preview_token: result.data.preview_token, keywords: selected }, { headers: { "X-CSRF-Token": admin.csrf_token } });
      notify.success("Keyword proposal awaiting review"); router.push(`/taxonomy/topics/proposals/${proposal.id}`);
    } catch (error) { setError(error instanceof Error ? error : new Error("Could not propose keywords")); notifyFailure(error, "Could not propose keywords"); setBusy(false); }
  }
  return <section className="space-y-6"><PageHeading title="Enrich topic keywords" trail={[...resourceTrail("topics"), { label: "Topics", href: "/taxonomy/topics" }, { label: result.data?.topic.name ?? "Topic", href: `/taxonomy/topics/${id}` }]} description="Suggestions come from existing tags on at least two approved, published articles in this topic. Select useful terms, then send them for review." />
    <RequestState loading={result.loading} error={result.error ?? error} />
    {result.data && <><div className="flex flex-wrap items-center justify-between gap-3"><p className="text-sm text-muted-foreground">Examined {result.data.articles_examined} articles, up to the latest 200. Current keywords: {result.data.topic.keywords?.join(", ") || "None"}.</p><Button variant="outline" onClick={() => { setSelected([]); setError(undefined); setRevision(v => v + 1); }} disabled={busy}>Refresh evidence</Button></div>
      <div className="space-y-3">{result.data.suggestions.length ? result.data.suggestions.map(suggestion => <section key={suggestion.keyword} className="rounded-lg border bg-card p-5"><label className="flex items-center gap-3 font-medium"><Input type="checkbox" checked={selected.includes(suggestion.keyword)} disabled={busy} onChange={event => setSelected(previous => event.target.checked ? [...previous, suggestion.keyword] : previous.filter(value => value !== suggestion.keyword))} />{suggestion.keyword}<span className="text-sm font-normal text-muted-foreground">{suggestion.article_count} articles</span></label><ul className="ml-7 mt-3 space-y-2 text-sm">{suggestion.articles.map(article => <li key={article.id}><Link className="text-primary hover:underline" href={`/content/articles/${article.id}`}>{article.title}</Link></li>)}</ul></section>) : <p className="rounded-lg border p-8 text-center text-sm text-muted-foreground">No additional keywords have enough reviewed evidence, or the topic already has 100 keywords. Publish reviewed articles with relevant tags, then try again.</p>}</div>
      <div className="flex justify-end gap-2"><Button variant="outline" asChild><Link href={`/taxonomy/topics/${id}`}>Cancel</Link></Button><Button onClick={submit} disabled={!selected.length || result.loading} loading={busy} loadingText="Submitting…">Send selected keywords for review</Button></div>
    </>}
  </section>;
}
