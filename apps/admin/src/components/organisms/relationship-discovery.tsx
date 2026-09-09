"use client";

import Link from "next/link";
import { useCallback, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Sparkles } from "lucide-react";
import { Button } from "@/components/atoms/button";
import { Field } from "@/components/molecules/field";
import { EntityPicker } from "@/components/molecules/entity-picker";
import { PageHeading } from "@/components/molecules/page-heading";
import { ValidationErrors } from "@/components/molecules/validation-errors";
import { useAdmin } from "@/components/molecules/admin-session";
import { adminTopicGet, adminTopicRelationshipsAnalyze } from "@/lib/api/generated/admin";
import { notify, notifyFailure } from "@/lib/notifications";
import { useRequest } from "@/lib/use-request";
import { recordHref, resourceTrail } from "@/lib/routes";

export const relationshipTrail = [...resourceTrail("topic-relations"), { label: "Topic relationships", href: "/taxonomy/relationships" }];

export function RelationshipDiscovery() {
  const admin = useAdmin(); const router = useRouter(); const search = useSearchParams();
  const [topic, setTopic] = useState(search.get("topic_id") ?? "");
  const [related, setRelated] = useState("");
  const [busy, setBusy] = useState(false); const [error, setError] = useState<Error>();
  const load = useCallback((signal: AbortSignal) => topic ? adminTopicGet(topic, { signal }) : Promise.resolve(null), [topic]);
  const current = useRequest(topic, load);
  async function submit() {
    if (busy || current.data?.status !== "active") return;
    setBusy(true); setError(undefined);
    try {
      const job = await adminTopicRelationshipsAnalyze(topic, { related_topic_id: related || null }, { headers: { "X-CSRF-Token": admin.csrf_token } });
      notify.success("Relationship research queued");
      router.push(recordHref("analysis-jobs", job));
    } catch (error) { setError(error instanceof Error ? error : new Error("Could not start research")); notifyFailure(error, "Could not start relationship research"); }
    finally { setBusy(false); }
  }
  return <section className="min-w-0 space-y-6">
    <PageHeading title="Discover relationships" trail={relationshipTrail} description="AI researches connections between active topics using public sources. Review suggestions before adding them.">
      <Button variant="outline" size="sm" asChild><Link href="/taxonomy/relationships/proposals">Review proposals</Link></Button>
    </PageHeading>
    <form className="max-w-2xl space-y-5 rounded-lg border bg-card p-5" onSubmit={event => { event.preventDefault(); void submit(); }}>
      <ValidationErrors error={error ?? current.error} />
      <Field label="Topic" required disabled={busy} error={current.data && current.data.status !== "active" ? "Select an active topic to research." : undefined}>
        {props => <EntityPicker {...props} resource="topics" label="Topic" status="active" value={topic} onChange={value => { setTopic(value); if (related === value) setRelated(""); }} />}
      </Field>
      <Field label="Related topic" disabled={busy} subtext="Optional. Leave empty to look for connections across all active topics.">
        {props => <EntityPicker {...props} resource="topics" label="Related topic" status="active" exclude={topic} value={related} onChange={setRelated} />}
      </Field>
      <div className="flex flex-wrap justify-end gap-2"><Button variant="outline" asChild><Link href="/taxonomy/relationships">Cancel</Link></Button>
        <Button type="submit" disabled={!topic || current.data?.status !== "active"} loading={busy} loadingText="Starting research…"><Sparkles aria-hidden />Discover with AI</Button></div>
    </form>
  </section>;
}
