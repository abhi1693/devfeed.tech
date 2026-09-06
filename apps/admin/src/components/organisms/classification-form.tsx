"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/atoms/button";
import { Input } from "@/components/atoms/input";
import { Field } from "@/components/molecules/field";
import { FormField } from "@/components/molecules/form-field";
import { ValidationErrors } from "@/components/molecules/validation-errors";
import { useAdmin } from "@/components/molecules/admin-session";
import { adminArticleClassify } from "@/lib/api/generated/admin";
import type { AdminArticleOut, ClassifyArticle } from "@/lib/api/generated/models";
import { contentFormats, contentTypes } from "@/lib/resources";
import { notify, notifyFailure } from "@/lib/notifications";

export function ClassificationForm({ article }: { article: AdminArticleOut }) {
  const admin = useAdmin(); const router = useRouter();
  const [body, setBody] = useState<ClassifyArticle>(() => ({
    expected_revision: article.editorial_revision, developer_relevance: existingRelevance(article), language: article.language ?? "",
    content_type: article.content_type as ClassifyArticle["content_type"], content_format: article.content_format as ClassifyArticle["content_format"],
    topics: (article.topics ?? []).map(topic => ({ topic_id: topic.topic_id, role: topic.role as ClassifyArticle["topics"][number]["role"], relevance: topic.relevance, evidence: topic.evidence })),
    categories: article.categories.map(item => ({ id: item.id, evidence: "" })), tags: article.tags.map(item => ({ id: item.id, evidence: "" })), note: null,
  }));
  const [error, setError] = useState<Error>(); const [busy, setBusy] = useState(false);
  const set = (key: string, value: unknown) => setBody(previous => ({ ...previous, [key]: value }));
  async function save(event: React.FormEvent) {
    event.preventDefault(); if (busy) return; setBusy(true); setError(undefined);
    try {
      await adminArticleClassify(article.id, body, { headers: { "X-CSRF-Token": admin.csrf_token } });
      notify.success("Classification saved", { description: "Approval has been reset. Review the article before publishing." });
      router.replace(`/articles/${article.id}`); router.refresh();
    } catch (error) { setError(error instanceof Error ? error : undefined); notifyFailure(error, "Could not save classification"); setBusy(false); }
  }
  return <form onSubmit={save} className="space-y-6 rounded-lg border bg-card p-6"><p className="text-sm text-muted-foreground">Classify the article itself, not related technologies in general. Each assignment needs a verbatim evidence quote from the title, original summary, or stored extraction. Saving resets approval and unpublishes the article.</p><ValidationErrors error={error} />
    <fieldset disabled={busy} className="space-y-6"><div className="grid gap-5 sm:grid-cols-2">
      <FormField field={{ key: "developer_relevance", label: "Developer relevance", type: "select", choices: ["relevant", "unrelated", "uncertain"], required: true }} value={body.developer_relevance} onChange={value => set("developer_relevance", value)} />
      <FormField field={{ key: "language", label: "Language", type: "language", required: true, max: 35 }} value={body.language} onChange={value => set("language", value)} />
      <FormField field={{ key: "content_type", label: "Content type", type: "select", choices: contentTypes, required: true }} value={body.content_type} onChange={value => set("content_type", value)} />
      <FormField field={{ key: "content_format", label: "Format", type: "select", choices: contentFormats, required: true }} value={body.content_format} onChange={value => set("content_format", value)} />
    </div>
    <section className="space-y-4"><h2 className="font-semibold">Topic relevance</h2>{body.topics.map((topic, index) => {
      const update = (key: string, value: unknown) => set("topics", body.topics.map((item, i) => i === index ? { ...item, [key]: value } : item));
      return <fieldset key={index} className="grid gap-4 rounded-md border p-4 sm:grid-cols-2"><legend className="px-1 text-sm">Topic {index + 1}</legend>
        <FormField field={{ key: `topic-${index}`, label: "Topic", type: "reference", resource: "topics", required: true }} value={topic.topic_id} onChange={value => update("topic_id", value)} />
        <FormField field={{ key: `role-${index}`, label: "Role", type: "select", choices: ["primary", "supporting", "comparison", "incidental"], required: true }} value={topic.role} onChange={value => update("role", value)} />
        <Field id={`relevance-${index}`} label="Relevance (0–1)" required>{control => <Input {...control} type="number" min="0" max="1" step="0.01" value={topic.relevance} onChange={event => update("relevance", Number(event.target.value))} />}</Field>
        <FormField field={{ key: `evidence-${index}`, label: "Evidence quote", required: true, max: 500 }} value={topic.evidence} onChange={value => update("evidence", value)} />
        <Button type="button" variant="destructive-ghost" className="justify-self-start" size="sm" onClick={() => set("topics", body.topics.filter((_, i) => i !== index))}>Remove topic</Button>
      </fieldset>;
    })}<Button variant="outline" type="button" disabled={body.topics.length >= 12} onClick={() => set("topics", [...body.topics, { topic_id: "", role: "primary", relevance: 1, evidence: "" }])}>Add topic</Button></section>
    {(["categories", "tags"] as const).map(key => <section key={key} className="space-y-4"><h2 className="font-semibold capitalize">{key}</h2>{body[key].map((item, index) => <fieldset key={index} className="grid gap-4 rounded-md border p-4 sm:grid-cols-2"><legend className="px-1 text-sm">Assignment {index + 1}</legend><FormField field={{ key: `${key}-${index}`, label: key === "tags" ? "Tag" : "Category", type: "reference", resource: key, required: true }} value={item.id} onChange={value => set(key, body[key].map((row, i) => i === index ? { ...row, id: value } : row))} /><FormField field={{ key: `${key}-${index}-evidence`, label: "Evidence quote", required: true, max: 500 }} value={item.evidence} onChange={value => set(key, body[key].map((row, i) => i === index ? { ...row, evidence: value } : row))} /><Button type="button" variant="destructive-ghost" size="sm" className="justify-self-start" onClick={() => set(key, body[key].filter((_, i) => i !== index))}>Remove assignment</Button></fieldset>)}<Button variant="outline" type="button" disabled={body[key].length >= (key === "tags" ? 20 : 12)} onClick={() => set(key, [...body[key], { id: "", evidence: "" }])}>Add {key === "tags" ? "tag" : "category"}</Button></section>)}
    <FormField field={{ key: "note", label: "Review note", type: "textarea", max: 1000 }} value={body.note ?? ""} onChange={value => set("note", value || null)} />
    </fieldset><Button type="submit" loading={busy} loadingText="Saving…">Save classification</Button>
  </form>;
}

function existingRelevance(article: AdminArticleOut): ClassifyArticle["developer_relevance"] {
  const value = article.classification_provenance?.developer_relevance;
  return value === "relevant" || value === "unrelated" || value === "uncertain" ? value : "uncertain";
}
