"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Button } from "@/components/atoms/button";
import { Input } from "@/components/atoms/input";
import { Textarea } from "@/components/atoms/textarea";
import { Field } from "@/components/molecules/field";
import { Select } from "@/components/molecules/select";
import { PageHeading } from "@/components/molecules/page-heading";
import { RequestState } from "@/components/molecules/request-state";
import { ApiError } from "@/lib/api/client";
import { useAdmin } from "@/components/molecules/admin-session";
import {
  adminPartnerToolsList,
  adminPartnerToolsImport,
  adminPartnerToolsImportNick,
  adminPartnerToolsReview,
  adminPartnerToolsEvaluate,
  adminPartnerToolsEvaluations,
  adminPartnerToolsMatchReview,
} from "@/lib/api/generated/admin";
import type {
  ProductInput,
  NickProduct,
  ProductOut,
  EvaluationOut,
  ProductReview,
} from "@/lib/api/generated/models";

const empty: ProductInput = {
  provider: "nick-launches",
  external_id: "",
  name: "",
  product_url: "",
  listing_url: "",
  description: "",
  pricing: "unknown",
  technologies: [],
  evidence: [],
  attribution: "Via Nick Launches",
};
const asError = (error: unknown) => {
  if (error instanceof ApiError && Object.keys(error.fields).length)
    return new Error(
      Object.entries(error.fields)
        .map(([field, message]) => `${field}: ${message}`)
        .join("; "),
    );
  return error instanceof Error ? error : new Error("Request failed. Try again.");
};

export function PartnerTools() {
  const admin = useAdmin();
  const options = { headers: { "X-CSRF-Token": admin.csrf_token } };
  const [items, setItems] = useState<ProductOut[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error>();
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<ProductOut>();
  const [draft, setDraft] = useState<ProductInput>(empty);
  const [editing, setEditing] = useState(false);
  const [importText, setImportText] = useState("");
  const [importFormat, setImportFormat] = useState("nick");
  const [runs, setRuns] = useState<EvaluationOut[]>([]);
  const [note, setNote] = useState("");
  const [checked, setChecked] = useState(false);
  const [rights, setRights] = useState(false);
  const [articleIds, setArticleIds] = useState("");
  const [message, setMessage] = useState("");

  async function load(page = offset) {
    const response = await adminPartnerToolsList({ offset: page, limit: 25 });
    setItems(response.items);
    setTotal(response.total);
    setSelected((current) =>
      current ? (response.items.find((item) => item.id === current.id) ?? current) : current,
    );
  }
  useEffect(() => {
    let live = true;
    adminPartnerToolsList({ offset, limit: 25 })
      .then((page) => {
        if (live) {
          setItems(page.items);
          setTotal(page.total);
        }
      })
      .catch((error) => {
        if (live) setError(asError(error));
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
  }, [offset]);

  async function action(work: () => Promise<void>) {
    setBusy(true);
    setError(undefined);
    setMessage("");
    try {
      await work();
    } catch (error) {
      setError(asError(error));
    } finally {
      setBusy(false);
    }
  }
  async function select(product: ProductOut) {
    setSelected(product);
    setEditing(false);
    setRuns([]);
    setNote("");
    setChecked(false);
    setRights(false);
    await action(async () => setRuns(await adminPartnerToolsEvaluations(product.id)));
  }
  function edit(product?: ProductOut) {
    setDraft(
      product
        ? {
            provider: product.provider,
            external_id: product.external_id,
            name: product.name,
            product_url: product.product_url,
            listing_url: product.listing_url,
            description: product.description,
            pricing: product.pricing,
            technologies: product.technologies,
            evidence: product.evidence,
            attribution: product.attribution,
          }
        : { ...empty, technologies: [], evidence: [] },
    );
    setEditing(true);
  }
  async function review(status: ProductReview["status"]) {
    if (!selected) return;
    await action(async () => {
      const product = await adminPartnerToolsReview(
        selected.id,
        {
          status,
          expected_revision: selected.revision,
          note,
          evidence_checked: checked,
          display_rights_confirmed: rights,
        },
        options,
      );
      setSelected(product);
      setNote("");
      setChecked(false);
      setRights(false);
      setRuns(await adminPartnerToolsEvaluations(product.id));
      await load();
      setMessage(`Product ${status}. Reader visibility remains off.`);
    });
  }
  const textField = (
    key: "provider" | "external_id" | "name" | "product_url" | "listing_url" | "attribution",
    label: string,
    type = "text",
  ) => (
    <Field label={label} required={key !== "attribution"}>
      {(control) => (
        <Input
          {...control}
          type={type}
          value={draft[key] ?? ""}
          disabled={busy || (!!selected && editing && ["provider", "external_id"].includes(key))}
          onChange={(e) => setDraft({ ...draft, [key]: e.target.value })}
        />
      )}
    </Field>
  );
  return (
    <section className="min-w-0 space-y-6">
      <PageHeading
        title="Partner tools"
        description="Review partner products and evaluate article relevance before introducing reader suggestions."
      />
      <p className="rounded-lg border bg-card p-4 text-sm">
        Evaluation only. Products and matches are private. Approval does not publish a product or
        change the reader feed.
      </p>
      <RequestState loading={loading} error={error} retry={() => void action(() => load())} />
      {message && <p role="status">{message}</p>}
      <div className="flex flex-wrap gap-2">
        <Button
          disabled={busy}
          onClick={() => {
            setSelected(undefined);
            edit();
          }}
        >
          Add product
        </Button>
        <Button
          variant="outline"
          disabled={busy}
          onClick={() =>
            void action(async () => {
              await load();
              if (selected) setRuns(await adminPartnerToolsEvaluations(selected.id));
            })
          }
        >
          Refresh
        </Button>
      </div>
      <details className="rounded-lg border bg-card p-4">
        <summary className="cursor-pointer font-medium">Import a product collection</summary>
        <form
          className="mt-4 space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            void action(async () => {
              if (new TextEncoder().encode(importText).length > 1_000_000)
                throw new Error("Use a collection smaller than 1 MB.");
              const parsed: unknown = JSON.parse(importText);
              const raw =
                parsed && typeof parsed === "object" && "results" in parsed
                  ? parsed.results
                  : parsed;
              if (!Array.isArray(raw))
                throw new Error("Provide a JSON array or a Nick API response with results.");
              if (importFormat === "nick")
                await adminPartnerToolsImportNick({ products: raw as NickProduct[] }, options);
              else await adminPartnerToolsImport({ products: raw as ProductInput[] }, options);
              setImportText("");
              await load();
              setMessage("Collection imported for review.");
            });
          }}
        >
          <p className="text-sm text-muted-foreground">
            Paste up to 50 products using the fields in the product form. Existing provider and
            external ID pairs are updated; changed products require verification again.
          </p>
          <Field label="Collection format">
            {(control) => (
              <Select
                {...control}
                label="Collection format"
                value={importFormat}
                onChange={setImportFormat}
                options={[
                  { value: "nick", label: "Nick Launches API JSON" },
                  { value: "devfeed", label: "DevFeed product JSON" },
                ]}
              />
            )}
          </Field>
          <Field label="Product collection JSON" required>
            {(control) => (
              <Textarea
                {...control}
                rows={6}
                maxLength={1000000}
                value={importText}
                onChange={(e) => setImportText(e.target.value)}
              />
            )}
          </Field>
          <Button disabled={busy} type="submit">
            Import for review
          </Button>
        </form>
      </details>
      <div className="grid gap-6 xl:grid-cols-[minmax(15rem,1fr)_minmax(0,3fr)]">
        <div className="space-y-3">
          <h2 className="font-semibold">Catalog ({total})</h2>
          {!loading && !items.length && (
            <p className="text-sm text-muted-foreground">
              No products yet. Add a reviewed sample or import a collection.
            </p>
          )}
          <ul className="space-y-2">
            {items.map((product) => (
              <li key={product.id}>
                <button
                  disabled={busy}
                  onClick={() => void select(product)}
                  aria-pressed={selected?.id === product.id}
                  className="w-full rounded-lg border bg-card p-3 text-left hover:bg-accent aria-pressed:border-primary"
                >
                  <span className="block font-medium">{product.name}</span>
                  <span className="text-sm text-muted-foreground">
                    {product.provider} · {product.status}
                    {product.status === "approved" && !product.eligible
                      ? " · verification expired"
                      : ""}
                  </span>
                </button>
              </li>
            ))}
          </ul>
          <div className="flex flex-wrap gap-2">
            <Button
              variant="outline"
              disabled={!offset || busy}
              onClick={() => {
                setLoading(true);
                setOffset(Math.max(0, offset - 25));
              }}
            >
              Previous
            </Button>
            <Button
              variant="outline"
              disabled={offset + 25 >= total || busy}
              onClick={() => {
                setLoading(true);
                setOffset(offset + 25);
              }}
            >
              Next
            </Button>
          </div>
        </div>
        <div className="min-w-0 space-y-6">
          {editing ? (
            <form
              className="space-y-4 rounded-lg border bg-card p-5"
              onSubmit={(event) => {
                event.preventDefault();
                void action(async () => {
                  const [product] = await adminPartnerToolsImport(
                    {
                      products: [
                        {
                          ...draft,
                          technologies: draft.technologies
                            ?.map((value) => value.trim())
                            .filter(Boolean),
                        },
                      ],
                    },
                    options,
                  );
                  setSelected(product);
                  setEditing(false);
                  setRuns(await adminPartnerToolsEvaluations(product.id));
                  await load();
                  setNote("");
                  setChecked(false);
                  setRights(false);
                  setMessage("Saved for review.");
                });
              }}
            >
              <h2 className="text-lg font-semibold">{selected ? "Edit product" : "Add product"}</h2>
              <div className="grid gap-4 sm:grid-cols-2">
                {textField("provider", "Provider")}
                {textField("external_id", "External ID")}
                {textField("name", "Product name")}
                {textField("attribution", "Attribution")}
                {textField("product_url", "Product URL", "url")}
                {textField("listing_url", "Partner listing URL", "url")}
              </div>
              <Field label="Description" required>
                {(control) => (
                  <Textarea
                    {...control}
                    minLength={10}
                    maxLength={5000}
                    value={draft.description}
                    onChange={(e) => setDraft({ ...draft, description: e.target.value })}
                  />
                )}
              </Field>
              <Field label="Pricing">
                {(control) => (
                  <Select
                    {...control}
                    label="Pricing"
                    value={draft.pricing ?? "unknown"}
                    options={["unknown", "free", "freemium", "paid"].map((value) => ({
                      value,
                      label: value,
                    }))}
                    onChange={(value) =>
                      setDraft({ ...draft, pricing: value as ProductInput["pricing"] })
                    }
                  />
                )}
              </Field>
              <Field
                label="Supported technologies"
                subtext="One specific technology per line, such as OpenAPI or PostgreSQL. Broad categories are insufficient."
              >
                {(control) => (
                  <Textarea
                    {...control}
                    value={(draft.technologies ?? []).join("\n")}
                    onChange={(e) =>
                      setDraft({ ...draft, technologies: e.target.value.split("\n") })
                    }
                  />
                )}
              </Field>
              <h3 className="font-medium">Capability evidence</h3>
              {(draft.evidence ?? []).map((evidence, index) => (
                <fieldset key={index} className="space-y-3 rounded border p-3">
                  <legend>Evidence {index + 1}</legend>
                  {(
                    [
                      ["url", "Evidence URL"],
                      ["capability", "Supported capability"],
                      ["quote", "Exact documentation quote"],
                    ] as const
                  ).map(([key, label]) => (
                    <Field key={key} label={label} required>
                      {(control) => (
                        <Textarea
                          {...control}
                          value={evidence[key]}
                          onChange={(e) =>
                            setDraft({
                              ...draft,
                              evidence: draft.evidence?.map((item, i) =>
                                i === index ? { ...item, [key]: e.target.value } : item,
                              ),
                            })
                          }
                        />
                      )}
                    </Field>
                  ))}
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() =>
                      setDraft({
                        ...draft,
                        evidence: draft.evidence?.filter((_, i) => i !== index),
                      })
                    }
                  >
                    Remove evidence {index + 1}
                  </Button>
                </fieldset>
              ))}
              <Button
                type="button"
                variant="outline"
                disabled={(draft.evidence?.length ?? 0) >= 10}
                onClick={() =>
                  setDraft({
                    ...draft,
                    evidence: [...(draft.evidence ?? []), { url: "", quote: "", capability: "" }],
                  })
                }
              >
                Add evidence
              </Button>
              <p className="text-sm text-muted-foreground">
                Saving changes invalidates verification and previous evaluations. Nothing is
                published.
              </p>
              <div className="flex flex-wrap gap-2">
                <Button disabled={busy} type="submit">
                  Save for review
                </Button>
                <Button type="button" variant="outline" onClick={() => setEditing(false)}>
                  Cancel
                </Button>
              </div>
            </form>
          ) : (
            selected && (
              <>
                <section className="space-y-4 rounded-lg border bg-card p-5">
                  <div className="flex items-center justify-between gap-4">
                    <h2 className="text-xl font-semibold">{selected.name}</h2>
                    <Button variant="outline" disabled={busy} onClick={() => edit(selected)}>
                      Edit product
                    </Button>
                  </div>
                  <p>{selected.description}</p>
                  <p className="text-sm">
                    {selected.pricing} ·{" "}
                    {selected.technologies?.join(", ") || "No technologies recorded"}
                  </p>
                  <div className="flex flex-wrap gap-4 text-sm">
                    <a
                      href={selected.product_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="underline"
                    >
                      Product website
                    </a>
                    <a
                      href={selected.listing_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="underline"
                    >
                      Partner listing
                    </a>
                  </div>
                  <p className="text-sm">
                    Last verified:{" "}
                    {selected.verified_at
                      ? new Date(selected.verified_at).toLocaleString()
                      : "Not verified"}
                    . Verification expires after 90 days.
                  </p>
                  {(selected.evidence ?? []).map((evidence, index) => (
                    <div key={index} className="space-y-2 border-l-2 pl-3">
                      <p className="font-medium">{evidence.capability}</p>
                      <blockquote className="text-sm">{evidence.quote}</blockquote>
                      <a
                        className="text-sm underline"
                        href={evidence.url}
                        target="_blank"
                        rel="noopener noreferrer"
                      >
                        Check evidence {index + 1}
                      </a>
                    </div>
                  ))}
                  <Field label="Review note" required>
                    {(control) => (
                      <Textarea
                        {...control}
                        minLength={10}
                        maxLength={2000}
                        value={note}
                        onChange={(e) => setNote(e.target.value)}
                      />
                    )}
                  </Field>
                  <label className="flex gap-2 text-sm">
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={(e) => setChecked(e.target.checked)}
                    />
                    I checked the capability evidence against its source.
                  </label>
                  <label className="flex gap-2 text-sm">
                    <input
                      type="checkbox"
                      checked={rights}
                      onChange={(e) => setRights(e.target.checked)}
                    />
                    Permission to display the supplied product content is confirmed.
                  </label>
                  <div className="flex flex-wrap gap-2">
                    {(["approved", "rejected", "paused", "withdrawn"] as const).map((status) => (
                      <Button
                        key={status}
                        variant={status === "approved" ? "default" : "outline"}
                        disabled={
                          busy ||
                          note.trim().length < 10 ||
                          (status === "approved" &&
                            (!checked ||
                              !rights ||
                              !selected.evidence?.length ||
                              !selected.technologies?.length))
                        }
                        onClick={() => void review(status)}
                      >
                        {
                          {
                            approved: "Approve",
                            rejected: "Reject",
                            paused: "Pause",
                            withdrawn: "Withdraw",
                          }[status]
                        }
                      </Button>
                    ))}
                  </div>
                  <details>
                    <summary className="cursor-pointer text-sm font-medium">Review history</summary>
                    <ul className="mt-2 space-y-2 text-sm">
                      {selected.reviews.map((entry, i) => (
                        <li key={i}>
                          {String(entry.at)} · {String(entry.action)} · {String(entry.note ?? "")}
                        </li>
                      ))}
                    </ul>
                  </details>
                </section>
                <section className="space-y-4 rounded-lg border bg-card p-5">
                  <h2 className="text-lg font-semibold">Evaluate article relevance</h2>
                  <p className="text-sm text-muted-foreground">
                    Test up to 20 published articles, including unrelated examples. Leave blank to
                    sample the latest 20. Only practical tutorials can produce positive suggestions.
                  </p>
                  <Field
                    label="Sample article IDs"
                    subtext="Optional: one article UUID per line. Include both likely matches and unrelated articles."
                  >
                    {(control) => (
                      <Textarea
                        {...control}
                        value={articleIds}
                        onChange={(e) => setArticleIds(e.target.value)}
                      />
                    )}
                  </Field>
                  <Button
                    disabled={busy || !selected.eligible}
                    onClick={() =>
                      void action(async () => {
                        await adminPartnerToolsEvaluate(
                          selected.id,
                          { article_ids: articleIds.split(/\s+/).filter(Boolean) },
                          options,
                        );
                        setRuns(await adminPartnerToolsEvaluations(selected.id));
                        setMessage("Evaluation queued. Refresh to view results.");
                      })
                    }
                  >
                    Run private evaluation
                  </Button>
                  {!selected.eligible && (
                    <p className="text-sm">
                      Approve current evidence before running an evaluation.
                    </p>
                  )}
                </section>
                <h2 className="font-semibold">Recent evaluations</h2>
                {!runs.length && (
                  <p className="text-sm text-muted-foreground">No evaluations yet.</p>
                )}
                {runs.map((run) => (
                  <Evaluation
                    key={run.id}
                    run={run}
                    busy={busy}
                    onReview={(articleId, decision, reviewNote) =>
                      action(async () => {
                        await adminPartnerToolsMatchReview(
                          selected.id,
                          run.id,
                          { article_id: articleId, decision, note: reviewNote },
                          options,
                        );
                        setRuns(await adminPartnerToolsEvaluations(selected.id));
                      })
                    }
                  />
                ))}
              </>
            )
          )}
        </div>
      </div>
    </section>
  );
}

function Evaluation({
  run,
  busy,
  onReview,
}: {
  run: EvaluationOut;
  busy: boolean;
  onReview: (articleId: string, decision: "accepted" | "rejected", note: string) => Promise<void>;
}) {
  const [notes, setNotes] = useState<Record<string, string>>({});
  const decisions = run.result?.decisions ?? [];
  return (
    <section className="space-y-4 rounded-lg border bg-card p-5">
      <h3 className="font-medium">
        {new Date(run.created_at).toLocaleString()} · {run.status}
        {!run.current ? " · Stale" : ""}
      </h3>
      {run.error && <p role="alert">{run.error}</p>}
      {run.status === "succeeded" && (
        <p className="text-sm">
          {decisions.filter((d) => d.relevant).length} proposed matches ·{" "}
          {decisions.filter((d) => !d.relevant).length} no-match results
        </p>
      )}
      {decisions.map((decision) => {
        const article = run.snapshot.articles.find((a) => a.id === decision.article_id);
        const evidence = run.snapshot.product.evidence?.[decision.evidence_index];
        const review = [...run.reviews]
          .reverse()
          .find((entry) => entry.article_id === decision.article_id);
        return (
          <div key={decision.article_id} className="space-y-3 border-t pt-4">
            <Link
              href={`/content/articles/${decision.article_id}`}
              className="font-medium underline"
            >
              {article?.title ?? decision.article_id}
            </Link>
            <p className="text-sm font-medium">
              {decision.relevant ? "Proposed match" : "No match"}
              {review ? ` · Review ${String(review.decision)}` : " · Awaiting review"}
            </p>
            <p className="text-sm">{decision.reason}</p>
            {decision.article_quote && (
              <blockquote className="border-l-2 pl-3 text-sm">{decision.article_quote}</blockquote>
            )}
            {evidence && (
              <p className="text-sm">
                Product evidence: {evidence.quote}{" "}
                <a
                  className="underline"
                  href={evidence.url}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Source
                </a>
              </p>
            )}
            <details>
              <summary className="cursor-pointer text-sm">Article evidence used</summary>
              <p className="mt-2 whitespace-pre-wrap text-sm">{article?.text}</p>
            </details>
            <Field label={`Assessment note for ${article?.title ?? decision.article_id}`}>
              {(control) => (
                <Textarea
                  {...control}
                  maxLength={2000}
                  value={notes[decision.article_id] ?? ""}
                  onChange={(e) => setNotes({ ...notes, [decision.article_id]: e.target.value })}
                />
              )}
            </Field>
            <div className="flex flex-wrap gap-2">
              {(["accepted", "rejected"] as const).map((value) => (
                <Button
                  key={value}
                  variant="outline"
                  disabled={
                    busy || !run.current || (notes[decision.article_id]?.trim().length ?? 0) < 10
                  }
                  onClick={() =>
                    void onReview(decision.article_id, value, notes[decision.article_id])
                  }
                >
                  {value === "accepted" ? "Agree with assessment" : "Disagree with assessment"}
                </Button>
              ))}
            </div>
          </div>
        );
      })}
    </section>
  );
}
