"use client";

import { useState } from "react";
import Link from "next/link";
import { ChevronDown, GitFork, LoaderCircle, Sparkles } from "lucide-react";
import { Button } from "@/components/atoms/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/atoms/popover";
import { RequestState } from "@/components/molecules/request-state";
import { useAdmin } from "@/components/molecules/admin-session";
import { adminTopicGithubPull } from "@/lib/api/generated/admin";
import type { GitHubPull } from "@/lib/api/generated/models";
import { notify } from "@/lib/notifications";

export function TopicDiscovery({ onComplete }: { onComplete: () => void }) {
  const admin = useAdmin();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error>();
  const [progress, setProgress] = useState("");
  const [created, setCreated] = useState(0);
  const [issues, setIssues] = useState<string[]>([]);
  const [resume, setResume] = useState<GitHubPull>();

  async function pull() {
    if (busy) return;
    setBusy(true); setError(undefined);
    let cursor: GitHubPull = resume ?? {};
    let added = resume ? created : 0;
    if (!resume) { setCreated(0); setIssues([]); }
    setProgress(resume ? "Continuing GitHub import…" : "Reading github/explore…");
    try {
      while (true) {
        setResume(cursor);
        const result = await adminTopicGithubPull(cursor, { headers: { "X-CSRF-Token": admin.csrf_token } });
        added += result.created;
        setCreated(added); setIssues(values => [...values, ...result.issues]);
        setProgress(`Checked ${result.processed} of ${result.total} topics. ${added} new proposals.`);
        if (result.next_offset === null) break;
        cursor = { revision: result.revision, offset: result.next_offset };
      }
      setResume(undefined);
      notify.success(`${added} GitHub topic proposals imported`);
    } catch (error) {
      setError(error instanceof Error ? error : new Error("Could not pull topics from GitHub"));
    } finally { setBusy(false); onComplete(); }
  }

  // Keep the job state outside the popover content so closing it never loses a
  // running import or the cursor needed to resume a failed batch.
  return <Popover>
    <PopoverTrigger asChild><Button size="sm" aria-label="Discover topics">
      {busy ? <LoaderCircle aria-hidden className="animate-spin motion-reduce:animate-none" /> : <Sparkles aria-hidden />}
      {busy ? "Pulling topics…" : "Discover topics"}<ChevronDown aria-hidden />
    </Button></PopoverTrigger>
    <PopoverContent align="end" aria-label="Discover topics" className="w-80 max-w-[calc(100vw-1.5rem)] p-2">
      <Button variant="ghost" aria-label={resume ? "Continue pulling" : "Pull from GitHub"} className="h-auto w-full justify-start px-3 py-3 text-left" disabled={busy} onClick={() => void pull()}>
        <GitFork aria-hidden className="size-5" /><span><span className="block">{resume ? "Continue pulling" : "Pull from GitHub"}</span><span className="mt-0.5 block text-xs font-normal text-muted-foreground">Curated topics from github/explore</span></span>
      </Button>
      <p className="border-t px-3 pt-3 pb-1 text-xs leading-relaxed text-muted-foreground">New topics follow your research and approval settings. Existing topics are skipped. The first batch downloads the catalog and can take a minute.</p>
      {(progress || error || issues.length > 0) && <div className="space-y-3 px-3 py-2 text-sm">
        {progress && <p role="status" className="text-muted-foreground">{progress}</p>}
        <RequestState error={error} />
        {issues.length > 0 && <details><summary className="cursor-pointer text-xs">{issues.length} topics need manual correction</summary><ul className="mt-2 max-h-40 space-y-1 overflow-y-auto text-xs text-muted-foreground">{issues.map((issue, index) => <li className="break-words" key={index}>{issue}</li>)}</ul></details>}
        {created > 0 && !busy && <Button variant="outline" size="sm" asChild><Link href="/taxonomy/topics/proposals?status=pending">Review imported topics</Link></Button>}
      </div>}
    </PopoverContent>
  </Popover>;
}
