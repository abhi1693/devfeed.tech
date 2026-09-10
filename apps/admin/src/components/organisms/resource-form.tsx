"use client";
import { resourceTrail, recordHref, resourceHref } from "@/lib/routes";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/atoms/button";
import { ValidationErrors } from "@/components/molecules/validation-errors";
import { FormField } from "@/components/molecules/form-field";
import { adminRouteTitle } from "@/lib/page-titles";
import { PageHeading } from "@/components/molecules/page-heading";
import { RequestState } from "@/components/molecules/request-state";
import { useAdmin } from "@/components/molecules/admin-session";
import { FactsEditor } from "./facts-editor";
import { SourceFormFields } from "./source-form-fields";
import { type Resource, resources } from "@/lib/resources";
import { getRecord, saveRecord, type RecordData } from "@/lib/resource-api";
import { factsPayload, formPayload, initialFacts, initialValues, type EditableFact } from "@/lib/form-values";
import { slugify } from "@/lib/slug";
import { useRequest } from "@/lib/use-request";
import { ApiError } from "@/lib/api/client";
import { notify, notifyFailure } from "@/lib/notifications";

export function ResourceForm({ resource, id }: { resource: Resource; id?: string }) {
  const [created, setCreated] = useState(0);
  const load = useCallback((signal: AbortSignal) => id ? getRecord(resource, id, signal) : Promise.resolve(null), [resource, id]);
  const { data, error, loading } = useRequest(`${resource}/${id}`, load);
  return <><RequestState loading={loading} error={error} />{!loading && !error && <Editor key={`${resource}/${id}/${created}`} resource={resource} record={data ?? undefined}
    focusFirstField={!id && created > 0} onCreateAnother={() => setCreated(value => value + 1)} />}</>;
}
function Editor({ resource, record, focusFirstField, onCreateAnother }: { resource: Resource; record?: RecordData; focusFirstField: boolean; onCreateAnother: () => void }) {
  const spec = resources[resource]; const router = useRouter(); const admin = useAdmin();
  const form = useRef<HTMLFormElement>(null);
  const saving = useRef(false);
  const [addingAnother, setAddingAnother] = useState(false);
  const [values, setValues] = useState(() => initialValues(resource, record));
  const [slugEdited, setSlugEdited] = useState(false);
  const [facts, setFacts] = useState(() => initialFacts(record?.facts as EditableFact[] | undefined));
  const [busy, setBusy] = useState(false); const [error, setError] = useState<Error>();
  const [previewBusy, setPreviewBusy] = useState(false);
  const cancel = record ? recordHref(resource, record) : resourceHref(resource);
  const title = `${record ? "Edit" : "Add"} ${spec.singular.toLowerCase()}`;
  useEffect(() => {
    if (focusFirstField) form.current?.querySelector<HTMLElement>('input:not([type="hidden"]):not(:disabled), textarea:not(:disabled), button[role="combobox"]:not(:disabled)')?.focus();
  }, [focusFirstField]);
  function changeField(key: string, value: unknown) {
    if (key === "slug") setSlugEdited(value !== "");
    const slugField = spec.fields.find(field => field.key === "slug");
    setValues(previous => ({
      ...previous,
      [key]: value,
      ...(!record && key === "name" && slugField && !slugEdited
        ? { slug: slugify(String(value), slugField.max) } : {}),
    }));
  }
  async function submit(event: React.FormEvent) {
    event.preventDefault(); if (saving.current || previewBusy) return;
    const addAnother = !record && (event.nativeEvent as SubmitEvent).submitter?.getAttribute("value") === "create-another";
    saving.current = true; setAddingAnother(addAnother); setBusy(true); setError(undefined);
    try {
      const body = formPayload(resource, values, record);
      if (resource === "topics") body.facts = factsPayload(facts);
      const result = await saveRecord(resource, body, admin.csrf_token, record?.id);
      notify.success(`${spec.singular} ${record ? "updated" : "created"}`);
      if (addAnother) { onCreateAnother(); return; }
      router.replace(recordHref(resource, result)); router.refresh();
    } catch (error) { setError(error instanceof Error ? error : new Error("Could not save")); notifyFailure(error, `Could not save ${spec.singular.toLowerCase()}`); saving.current = false; setBusy(false); }
  }
  return <section className="max-w-4xl space-y-6"><PageHeading title={title} browserTitle={adminRouteTitle(record ? { view: "edit", resource, id: record.id } : { view: "new", resource }, record?.[spec.title])} trail={[...resourceTrail(resource), { label: spec.label, href: resourceHref(resource) }, ...(record ? [{ label: String(record[spec.title]), href: cancel }] : [])]} description={resource === "articles" ? "Saving changed metadata resets approval and unpublishes the article. AI-generated prose remains distinct from original metadata." : undefined} />
    <form ref={form} onSubmit={submit} className="space-y-6 rounded-lg border bg-card p-6" aria-busy={busy || previewBusy}>
      <ValidationErrors error={error} inlineFields={spec.fields.map(field => field.key)} />
      {resource === "sources" ? <fieldset disabled={busy}><SourceFormFields values={values} onValuesChange={setValues} editing={!!record} disabled={busy} errors={error instanceof ApiError ? error.fields : undefined} onPreviewBusyChange={setPreviewBusy} /></fieldset> : <fieldset disabled={busy} className="grid gap-6 sm:grid-cols-2">{spec.fields.map(field => <div key={field.key} className={field.type === "textarea" || field.type === "lines" ? "sm:col-span-2" : ""}><FormField field={field} value={values[field.key]} onChange={value => changeField(field.key, value)} disabled={(!!record && field.createOnly) || (resource === "tags" && field.key === "topic_id" && values.auto_link_topic === true)} error={error instanceof ApiError ? error.fields[field.key] : undefined} /></div>)}</fieldset>}
      {resource === "topics" && <FactsEditor facts={facts} onChange={setFacts} disabled={busy} />}
      <div className="flex flex-wrap justify-end gap-2 border-t pt-5">
        <Button variant="outline" asChild><Link href={cancel} prefetch={false}>Cancel</Link></Button>
        <Button type="submit" disabled={busy || previewBusy} loading={busy && !addingAnother} loadingText={resource === "sources" && !record ? "Validating feed…" : "Saving…"}>{record ? "Save changes" : `Create ${spec.singular.toLowerCase()}`}</Button>
        {!record && <Button type="submit" name="intent" value="create-another" variant="outline" disabled={busy || previewBusy} loading={busy && addingAnother} loadingText={resource === "sources" ? "Validating feed…" : "Creating…"}>Create and add another</Button>}
      </div>
    </form>
  </section>;
}
