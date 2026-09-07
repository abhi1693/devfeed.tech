"use client";

import { resourceTrail } from "@/lib/routes";
import Link from "next/link";
import { useCallback, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Upload, X } from "lucide-react";
import { TopicAnalysisControl, analysisActive } from "@/components/molecules/topic-analysis-control";
import { TopicResearchEvidence } from "@/components/molecules/topic-research-evidence";
import { TopicBulkAnalysis } from "@/components/organisms/topic-bulk-analysis";
import { TopicDiscovery } from "@/components/organisms/topic-discovery";
import { TopicProposalsTable } from "@/components/organisms/topic-proposals-table";
import { TopicProposalFilters, readProposalFilters, clearedProposalFilters } from "@/components/organisms/topic-proposal-filters";
import { Button } from "@/components/atoms/button";
import { Input } from "@/components/atoms/input";
import { Textarea } from "@/components/atoms/textarea";
import { PageHeading } from "@/components/molecules/page-heading";
import { RequestState } from "@/components/molecules/request-state";
import { ValidationErrors } from "@/components/molecules/validation-errors";
import { useAdmin } from "@/components/molecules/admin-session";
import { adminTopicProposalsList, adminTopicProposalGet, adminTopicProposalReview } from "@/lib/api/generated/admin";
import type { TopicDraft, TopicProposalOut } from "@/lib/api/generated/models";
import { actorLabel } from "@/lib/actor-label";
import { useRequest } from "@/lib/use-request";
import { notify, notifyFailure } from "@/lib/notifications";

export function TopicProposals() {
  const router = useRouter();
  const search = useSearchParams();
  const [revision, setRevision] = useState(0);
  const status = (["pending", "approved", "rejected"].includes(search.get("status") ?? "") ? search.get("status") : "pending") as "pending" | "approved" | "rejected";
  const batchId = search.get("batch_id") || undefined;
  const q = (search.get("q") ?? "").slice(0, 200).trim();
  const { kind, source, action, analysis, missing } = readProposalFilters(search);
  const filtered = !!(q || batchId || kind || source || action || analysis || missing);
  const requestedOffset = Number(search.get("offset"));
  const offset = Number.isSafeInteger(requestedOffset) && requestedOffset >= 0 ? requestedOffset : 0;
  const limit = [10, 25, 50, 100].includes(Number(search.get("limit"))) ? Number(search.get("limit")) : 25;
  const sort = ["slug", "-slug", "created_at", "-created_at"].includes(search.get("sort") ?? "") ? search.get("sort")! : "-created_at";
  const refresh = useCallback(() => setRevision(value => value + 1), []);
  const load = useCallback((signal: AbortSignal) => adminTopicProposalsList({ status, batch_id: batchId, q, kind, source, action, analysis, missing, sort, offset, limit }, { signal }), [status, batchId, q, kind, source, action, analysis, missing, sort, offset, limit]);
  const result = useRequest(JSON.stringify([status, batchId, q, kind, source, action, analysis, missing, sort, offset, limit, revision]), load);

  function href(values: Record<string, string>) {
    const params = new URLSearchParams(search);
    for (const [key, value] of Object.entries(values)) {
      if (value) params.set(key, value); else params.delete(key);
    }
    return `/taxonomy/topics/proposals?${params}`;
  }
  function change(values: Record<string, string>) { router.push(href(values), { scroll: false }); }

  return <section className="min-w-0 space-y-6">
    <PageHeading title="Topic proposals" trail={[...resourceTrail("topics"), { label: "Topics", href: "/taxonomy/topics" }]} description="Review suggestions before they become active topics.">
      <Button variant="outline" size="sm" asChild><Link href="/taxonomy/topics/import"><Upload aria-hidden />Import</Link></Button>
      <TopicDiscovery onComplete={refresh} />
      {status === "pending" && <TopicBulkAnalysis onQueued={refresh} />}
    </PageHeading>
    <nav aria-label="Proposal status" className="flex gap-6 border-b">
      {(["pending", "approved", "rejected"] as const).map(value => <Link key={value} prefetch={false} scroll={false}
        href={href({ status: value, offset: "0" })} aria-current={status === value ? "page" : undefined}
        className={`flex items-center gap-2 border-b-2 px-1 pb-3 text-sm ${status === value ? "border-primary font-medium" : "border-transparent text-muted-foreground hover:text-foreground"}`}>
        {value[0].toUpperCase() + value.slice(1)}
        {status === value && result.data && <span className="rounded-md bg-muted px-1.5 py-0.5 text-xs tabular-nums">{result.data.total}</span>}
      </Link>)}
    </nav>
    <TopicProposalsTable toolbar={<TopicProposalFilters filters={{ kind, source, action, analysis, missing }} q={q} batchId={batchId}
      revision={revision} loading={result.loading} onChange={change} onRefresh={refresh} />}
      page={result.data} loading={result.loading} error={result.error} status={status} filtered={filtered}
      sort={sort} limit={limit} offset={offset} onChange={change} onRetry={refresh} onClearFilters={() => change(clearedProposalFilters)} />
  </section>;
}

export function TopicProposalReview({ id }: { id: string }) {
  const load = useCallback((signal: AbortSignal) => adminTopicProposalGet(id, { signal }), [id]);
  const result = useRequest(id, load);
  return <><RequestState loading={result.loading} error={result.error} />{result.data && <Review key={result.data.id} initial={result.data} />}</>;
}

const labels: Record<keyof TopicDraft, string> = { name: "Name", slug: "Slug", description: "Description", keywords: "Keywords", kind: "Kind", aliases: "Aliases", website_url: "Website", logo_url: "Logo URL", facts: "Sourced facts" };
function Evidence({ proposal }: { proposal: TopicProposalOut }) {
  return <div className="space-y-3">{proposal.evidence.map((item, index) => <div key={index} className="rounded-md border p-3 text-sm">{item.provider === "ai_topic_research" ? <TopicResearchEvidence evidence={item} /> : "row" in item ? <p>Imported row {String(item.row)} from {proposal.source_name}</p> : "source_url" in item && item.provider === "github/explore" && typeof item.source_url === "string" && item.source_url.startsWith("https://github.com/github/explore/blob/") ? <a className="text-primary hover:underline" href={item.source_url} target="_blank" rel="noopener noreferrer">GitHub curated source</a> : "quote" in item ? <><p className="whitespace-pre-wrap">{String(item.quote)}</p><Link className="text-primary hover:underline" href={`/content/articles/${encodeURIComponent(String(item.article_id))}`}>View source article</Link></> : "migration" in item ? <p>Migrated proposal; original audit record retained.</p> : <><p className="font-medium">{String(item.keyword)} · {String(item.article_count)} supporting articles</p><ul className="mt-2 space-y-1">{Array.isArray(item.articles) && item.articles.map((article, articleIndex) => {
    if (!article || typeof article !== "object" || !("title" in article) || !("id" in article)) return null;
    return <li key={articleIndex}><Link className="text-primary hover:underline" href={`/content/articles/${encodeURIComponent(String(article.id))}`}>{String(article.title)}</Link></li>;
  })}</ul></>}</div>)}</div>;
}

function Review({ initial }: { initial: TopicProposalOut }) {
  const admin = useAdmin(); const [proposal, setProposal] = useState(initial);
  const [draft, setDraft] = useState<TopicDraft>(initial.proposed);
  const [aliases, setAliases] = useState(initial.proposed.aliases?.join("\n") ?? "");
  const [keywords, setKeywords] = useState(initial.proposed.keywords?.join("\n") ?? "");
  const [note, setNote] = useState(""); const [busy, setBusy] = useState(false); const [error, setError] = useState<Error>();
  const [analyzing, setAnalyzing] = useState(analysisActive(initial.analysis));
  const dirty = JSON.stringify(draft) !== JSON.stringify(proposal.proposed) || aliases !== (proposal.proposed.aliases?.join("\n") ?? "") || keywords !== (proposal.proposed.keywords?.join("\n") ?? "");
  const analysisUpdated = useCallback((value: TopicProposalOut) => {
    setProposal(value); setDraft(value.proposed);
    setAliases(value.proposed.aliases?.join("\n") ?? "");
    setKeywords(value.proposed.keywords?.join("\n") ?? "");
  }, []);
  const reviewed = proposal.status !== "pending";
  const fields = reviewed ? proposal.applied ?? proposal.proposed : draft;
  async function review(decision: "approved" | "rejected") {
    if (reviewed || busy || analyzing) return; setBusy(true); setError(undefined);
    try {
      const topic = { ...draft, aliases: [...new Set(aliases.split(/\r?\n/).map(v => v.trim()).filter(Boolean))], keywords: [...new Set(keywords.split(/\r?\n/).map(v => v.trim()).filter(Boolean))] };
      const result = await adminTopicProposalReview(proposal.id, { decision, ...(proposal.content_hash ? { expected_input_hash: proposal.content_hash } : {}), note: note.trim() || null, ...(decision === "approved" ? { topic } : {}) }, { headers: { "X-CSRF-Token": admin.csrf_token } });
      setProposal(result); notify.success(decision === "approved" ? "Topic changes approved and applied" : "Topic proposal rejected");
    } catch (error) { setError(error instanceof Error ? error : new Error("Review failed")); notifyFailure(error, "Could not review proposal"); }
    finally { setBusy(false); }
  }
  return <section className="min-w-0 space-y-6">
    <PageHeading title={reviewed ? fields.name : proposal.proposed.name} trail={[...resourceTrail("topics"), { label: "Topics", href: "/taxonomy/topics" }, { label: "Proposals", href: "/taxonomy/topics/proposals" }]} description={`${proposal.action === "create" ? "New topic" : "Topic update"} · ${proposal.status}`}>
      {proposal.status === "approved" && proposal.topic_id && <Button asChild><Link href={`/taxonomy/topics/${proposal.topic_id}`}>Open topic</Link></Button>}
    </PageHeading>
    <div className="grid min-w-0 grid-cols-1 items-start gap-6 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
      <div className="min-w-0">
        <form aria-label={reviewed ? "Topic details" : "Review and edit"} onSubmit={event => { event.preventDefault(); void review("approved"); }} className="space-y-5 rounded-lg border bg-card p-5">
          <h2 className="font-semibold">{reviewed ? (proposal.status === "approved" ? "Approved topic" : "Rejected proposal") : "Review and edit"}</h2>{!reviewed && <p className="text-sm text-muted-foreground">Keywords affect future automatic classification. Approval applies the fields below immediately.</p>}
          <ValidationErrors error={error} />
          <fieldset disabled={reviewed || busy || analyzing} className="grid gap-5 sm:grid-cols-2">{(["name", "slug", "kind", "description", "website_url", "logo_url"] as const).map(key => <label key={key} className={`space-y-2 text-sm font-medium ${key === "description" ? "sm:col-span-2" : ""}`}>{labels[key]}{key === "description" ? <Textarea maxLength={2000} value={fields.description ?? ""} onChange={event => setDraft(previous => ({ ...previous, description: event.target.value || null }))} /> : <Input required={key === "name" || key === "slug" || key === "kind"} maxLength={key === "website_url" || key === "logo_url" ? 2048 : 100} value={fields[key] ?? ""} onChange={event => setDraft(previous => ({ ...previous, [key]: event.target.value || (key === "name" || key === "slug" || key === "kind" ? "" : null) }))} />}</label>)}
            <label className="space-y-2 text-sm font-medium sm:col-span-2">Aliases<Textarea rows={3} value={reviewed ? fields.aliases?.join("\n") ?? "" : aliases} onChange={event => setAliases(event.target.value)} /><span className="text-xs font-normal text-muted-foreground">Alternative names for this same subject, one per line.</span></label>
            <label className="space-y-2 text-sm font-medium sm:col-span-2">Keywords<Textarea rows={5} value={reviewed ? fields.keywords?.join("\n") ?? "" : keywords} onChange={event => setKeywords(event.target.value)} /><span className="text-xs font-normal text-muted-foreground">One keyword per line, up to 100 terms.</span></label>
            {!!fields.facts?.length && <div className="space-y-2 sm:col-span-2"><p className="text-sm font-medium">Sourced facts</p>{fields.facts.map((fact, index) => <div key={index} className="flex items-start justify-between gap-3 rounded-md border p-3 text-sm"><div><p className="font-medium">{fact.name}</p><p>{fact.value}</p><a className="text-xs text-primary hover:underline" href={fact.source_url} target="_blank" rel="noopener noreferrer">Source</a></div>{!reviewed && <Button variant="ghost" size="icon-sm" aria-label={`Remove fact ${fact.name}`} onClick={() => setDraft(value => ({ ...value, facts: value.facts?.filter((_, i) => i !== index) }))}><X aria-hidden /></Button>}</div>)}</div>}
            {!reviewed && <label className="space-y-2 text-sm font-medium sm:col-span-2">Review note<Textarea maxLength={1000} value={note} onChange={event => setNote(event.target.value)} /></label>}
          </fieldset>{!reviewed && <div className="flex flex-wrap justify-end gap-2">{dirty && <Button variant="outline" disabled={busy || analyzing} onClick={() => analysisUpdated(proposal)}>Discard edits</Button>}<Button variant="destructive-ghost" onClick={() => void review("rejected")} disabled={busy || analyzing}>Reject proposal</Button><Button type="submit" disabled={analyzing} loading={busy} loadingText="Saving review…">{proposal.action === "create" ? "Approve and create topic" : "Approve and apply changes"}</Button></div>}
        </form>
      </div><aside id="proposal-evidence" className="min-w-0 space-y-5"><TopicAnalysisControl proposal={proposal} disabled={!reviewed && (busy || dirty)} onUpdated={analysisUpdated} onBusyChange={setAnalyzing} /><section className="space-y-2 rounded-lg border bg-card p-5 text-sm"><h2 className="font-semibold">Provenance</h2><p className="break-words">{proposal.source_name}</p><p className="break-words text-muted-foreground">Submitted by {actorLabel(proposal.created_by, admin)} · {new Date(proposal.created_at).toLocaleString()}</p>{proposal.reviewed_at && proposal.reviewed_by && <p className="break-words">Reviewed by {actorLabel(proposal.reviewed_by, admin)} · {new Date(proposal.reviewed_at!).toLocaleString()}</p>}{proposal.review_note && <p className="whitespace-pre-wrap">{proposal.review_note}</p>}</section><Evidence proposal={proposal} /></aside>
    </div>
  </section>;
}
