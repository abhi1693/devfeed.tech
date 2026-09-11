"use client";

import { resourceTrail } from "@/lib/routes";
import Link from "next/link";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/atoms/button";
import { Input } from "@/components/atoms/input";
import { Textarea } from "@/components/atoms/textarea";
import { Select } from "@/components/molecules/select";
import { Field } from "@/components/molecules/field";
import { FormField } from "@/components/molecules/form-field";
import { PageHeading } from "@/components/molecules/page-heading";
import { DataTable, type DataTableColumn } from "@/components/molecules/data-table";
import { RequestState } from "@/components/molecules/request-state";
import { useAdmin } from "@/components/molecules/admin-session";
import { adminTopicImportPreview, adminTopicImportSubmit } from "@/lib/api/generated/admin";
import type { TopicImport as ImportBody, TopicImportPreview } from "@/lib/api/generated/models";
import { notify, notifyFailure } from "@/lib/notifications";

const previewColumns: DataTableColumn<TopicImportPreview["rows"][number]>[] = [
  { accessorKey: "row", header: "Row" },
  { id: "topic", header: "Topic", cell: ({ row: { original } }) => <>{original.topic?.name ?? "Invalid row"}<p className="text-xs text-muted-foreground">{original.topic?.slug}</p></> },
  { accessorKey: "action", header: "Change", meta: { className: "capitalize" } },
  { id: "issues", header: "Review notes", meta: { className: "whitespace-normal min-w-48" },
    cell: ({ row: { original } }) => original.issues?.length ? <ul className="space-y-1 text-destructive">{original.issues.map(issue => <li key={issue}>{issue}</li>)}</ul> : "Ready for review" },
];

const example = '[\n  {"name": "Backend engineering", "slug": "backend-engineering", "kind": "discipline", "description": "Server applications and API design.", "keywords": ["backend", "api design"]}\n]';

export function TopicImport() {
  const admin = useAdmin(); const router = useRouter();
  const [body, setBody] = useState<ImportBody>({ format: "json", source_name: "topics.json", content: "" });
  const [preview, setPreview] = useState<TopicImportPreview>();
  const [busy, setBusy] = useState(false); const [error, setError] = useState<Error>();
  function change(values: Partial<ImportBody>) { setBody(previous => ({ ...previous, ...values })); setPreview(undefined); setError(undefined); }
  async function fileSelected(file?: File) {
    if (!file) return;
    setPreview(undefined); setError(undefined); setBusy(true);
    try {
      if (file.size > 262144) throw new Error("Choose a file no larger than 256 KiB.");
      const content = await file.text();
      change({ source_name: file.name, format: file.name.toLowerCase().endsWith(".csv") ? "csv" : "json", content });
    } catch (error) { setError(error instanceof Error ? error : new Error("Could not read file")); }
    finally { setBusy(false); }
  }
  async function inspect(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError(undefined); setPreview(undefined);
    try { setPreview(await adminTopicImportPreview(body, { headers: { "X-CSRF-Token": admin.csrf_token } })); }
    catch (error) { setError(error instanceof Error ? error : new Error("Could not preview import")); notifyFailure(error, "Could not preview import"); }
    finally { setBusy(false); }
  }
  async function submit() {
    if (!preview?.can_submit || busy) return;
    setBusy(true); setError(undefined);
    try {
      const result = await adminTopicImportSubmit({ ...body, preview_token: preview.preview_token }, { headers: { "X-CSRF-Token": admin.csrf_token } });
      notify.success(`${result.length} topic proposals awaiting review`);
      router.push(`/taxonomy/topics?view=proposals&batch_id=${encodeURIComponent(result[0].batch_id)}`);
    } catch (error) { setError(error instanceof Error ? error : new Error("Could not submit import")); notifyFailure(error, "Could not submit import"); setPreview(undefined); setBusy(false); }
  }
  return <section className="space-y-6">
    <PageHeading title="Import topics" trail={[...resourceTrail("topics"), { label: "Topics", href: "/taxonomy/topics" }]} description="Preview a catalog, then send changes for individual admin review. Importing creates proposals; topics become available after approval." />
    <form onSubmit={inspect} className="space-y-5 rounded-lg border bg-card p-6">
      <fieldset disabled={busy} className="space-y-5">
        <div className="grid gap-5 sm:grid-cols-2">
          <Field name="file" label="Choose a file" disabled={busy}>{control => <Input {...control} type="file" accept=".json,.csv,application/json,text/csv" onChange={event => void fileSelected(event.target.files?.[0])} />}</Field>
          <Field name="format" label="Format" required disabled={busy}>{control => <Select {...control} label="Format" value={body.format} onChange={value => change({ format: value as ImportBody["format"] })} options={[{ value: "json", label: "JSON" }, { value: "csv", label: "CSV" }]} />}</Field>
        </div>
        <FormField field={{ key: "source_name", label: "Source name", required: true, max: 200 }} value={body.source_name} onChange={value => change({ source_name: String(value) })} disabled={busy} />
        <Field name="content" label="Topic data" required disabled={busy} subtext="Up to 100 rows and 256 KiB. Required fields: name, slug, and kind. Optional: description, aliases, keywords, website_url, logo_url, and facts. CSV aliases and keywords use | between terms; facts use a JSON array. Omitted fields preserve existing values; empty optional fields clear them.">
          {control => <Textarea {...control} rows={10} maxLength={262144} value={body.content} onChange={event => change({ content: event.target.value })} className="font-mono text-xs" />}
        </Field>
        <div className="flex flex-wrap justify-between gap-3"><Button variant="ghost" onClick={() => change({ format: "json", source_name: "example.json", content: example })}>Load example</Button><Button type="submit" loading={busy} loadingText="Checking…">Preview import</Button></div>
      </fieldset>
    </form>
    <RequestState error={error} />
    {preview && <section className="space-y-4" aria-label="Import preview">
      <div><h2 className="text-lg font-semibold">Proposed changes</h2><p className="text-sm text-muted-foreground">Existing slugs update their topics. Unchanged rows are skipped. Names, slugs, and aliases must identify a single topic.</p></div>
      <DataTable label="Import preview" data={preview.rows} columns={previewColumns} getRowId={row => String(row.row)} rowClassName="align-top" />
      {!preview.can_submit && <p role="status" className="text-sm">Resolve the listed issues, or supply changed topics, then preview again.</p>}
      <div className="flex justify-end gap-2"><Button variant="outline" asChild><Link href="/taxonomy/topics">Cancel</Link></Button><Button onClick={submit} disabled={!preview.can_submit} loading={busy} loadingText="Submitting…">Send proposals for review</Button></div>
    </section>}
  </section>;
}
