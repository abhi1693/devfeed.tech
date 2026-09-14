"use client";

import Link from "next/link";
import { useState } from "react";
import { Button } from "@/components/atoms/button";
import { Input } from "@/components/atoms/input";
import { Textarea } from "@/components/atoms/textarea";
import { Field } from "@/components/molecules/field";
import { Select } from "@/components/molecules/select";
import { PageHeading } from "@/components/molecules/page-heading";
import { RequestState } from "@/components/molecules/request-state";
import { useAdmin } from "@/components/molecules/admin-session";
import { resourceTrail } from "@/lib/routes";
import { adminSourceImportSubmit } from "@/lib/api/generated/admin";
import type { SourceImportRequest } from "@/lib/api/generated/models";

const trail = [...resourceTrail("sources"), { label: "Sources", href: "/content/sources" }];
const examples = {
  opml: '<opml version="2.0"><body><outline text="Engineering" htmlUrl="https://example.com/blog" xmlUrl="https://example.com/feed" /></body></opml>',
  urls: "https://example.com/blog\nhttps://another.example/engineering",
  json: '[{"name":"Engineering","homepage_url":"https://example.com/blog","feed_url":"https://example.com/feed"}]',
  markdown: "[Engineering blog](https://example.com/blog)",
};
const asError = (error: unknown) =>
  error instanceof Error ? error : new Error("Request failed. Try again.");

export function SourceImport() {
  const admin = useAdmin();
  const [method, setMethod] = useState("url");
  const [format, setFormat] = useState<SourceImportRequest["format"]>("opml");
  const [url, setUrl] = useState("");
  const [content, setContent] = useState("");
  const [filename, setFilename] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error>();
  const [result, setResult] = useState<string>();

  async function fileSelected(file?: File) {
    setError(undefined);
    setContent("");
    setFilename("");
    if (!file) return;
    setBusy(true);
    try {
      if (file.size > 1_000_000) throw new Error("Choose a file no larger than 1 MB.");
      setContent(await file.text());
      setFilename(file.name);
      const extension = file.name.split(".").pop()?.toLowerCase();
      setFormat(
        extension === "json"
          ? "json"
          : extension === "md"
            ? "markdown"
            : extension === "txt"
              ? "urls"
              : "opml",
      );
    } catch (error) {
      setError(asError(error));
    } finally {
      setBusy(false);
    }
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(undefined);
    setResult(undefined);
    try {
      if (method !== "url" && new TextEncoder().encode(content).length > 1_000_000)
        throw new Error("Content must be no larger than 1 MB.");
      const response = await adminSourceImportSubmit(
        {
          format,
          name: filename || "Pasted collection",
          ...(method === "url" ? { url } : { content }),
        },
        { headers: { "X-CSRF-Token": admin.csrf_token } },
      );
      setResult(`${response.created} new publishers imported; ${response.existing} already known.`);
    } catch (error) {
      setError(asError(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="min-w-0 space-y-8">
      <PageHeading
        title="Import sources"
        trail={trail}
        description="Add sources from a link, file, or pasted list."
      />
      <form onSubmit={submit} className="space-y-5 rounded-lg border bg-card p-6">
        <div>
          <h2 className="text-lg font-semibold">Add a collection</h2>
        </div>
        <fieldset disabled={busy} className="space-y-5">
          <div className="grid gap-5 sm:grid-cols-2">
            <Field label="Import from" name="method" required>
              {(control) => (
                <Select
                  {...control}
                  label="Import from"
                  value={method}
                  onChange={(value) => {
                    setMethod(value);
                    setError(undefined);
                    setResult(undefined);
                  }}
                  options={[
                    { value: "url", label: "Collection URL" },
                    { value: "file", label: "Upload a file" },
                    { value: "paste", label: "Paste content" },
                  ]}
                />
              )}
            </Field>
            <Field label="Format" name="format">
              {(control) => (
                <Select
                  {...control}
                  label="Format"
                  value={format}
                  onChange={(value) => setFormat(value as SourceImportRequest["format"])}
                  options={[
                    { value: "opml", label: "OPML" },
                    { value: "urls", label: "Publisher URL list" },
                    { value: "json", label: "JSON" },
                    { value: "markdown", label: "Markdown links" },
                  ]}
                />
              )}
            </Field>
          </div>
          {method === "url" ? (
            <Field
              key="url"
              label="Collection URL"
              name="url"
              required
              subtext="Use a direct public file URL, such as a GitHub raw OPML URL. Publisher pages belong in a URL list."
            >
              {(control) => (
                <Input
                  {...control}
                  type="url"
                  value={url}
                  onChange={(event) => setUrl(event.target.value)}
                  placeholder="https://example.com/publishers.opml"
                />
              )}
            </Field>
          ) : method === "file" ? (
            <Field
              key="file"
              label="Collection file"
              name="file"
              required
              subtext="OPML, XML, TXT, JSON, or Markdown. Maximum 1 MB and 1,000 entries."
            >
              {(control) => (
                <Input
                  {...control}
                  type="file"
                  accept=".opml,.xml,.txt,.json,.md"
                  onChange={(event) => void fileSelected(event.target.files?.[0])}
                />
              )}
            </Field>
          ) : (
            <Field
              key="paste"
              label="Collection content"
              name="content"
              required
              subtext="Up to 1 MB and 1,000 entries. URL lists contain one publisher homepage per line."
            >
              {(control) => (
                <Textarea
                  {...control}
                  rows={7}
                  value={content}
                  onChange={(event) => setContent(event.target.value)}
                  placeholder={examples[format]}
                  className="font-mono text-sm"
                />
              )}
            </Field>
          )}
          <Button
            type="submit"
            disabled={busy || (method === "url" ? !url.trim() : !content.trim())}
          >
            {busy ? "Importing collection…" : "Import for review"}
          </Button>
        </fieldset>
        <RequestState error={error} />
        {result && (
          <p role="status" className="rounded-md border bg-muted/40 p-3 text-sm">
            {result}
          </p>
        )}
      </form>
      <Button asChild variant="outline">
        <Link href="/content/sources?approval_status=pending">View pending sources</Link>
      </Button>
    </section>
  );
}
