/** UI descriptions, not domain data: topic/tag choices always come from the API. */
export type Resource = "articles" | "sources" | "topics" | "tags" | "topic-relations" | "ingestion-jobs" | "article-jobs" | "image-jobs" | "source-jobs" | "analysis-jobs" | "notification-jobs";
export type ContentResource = "articles" | "sources" | "topics" | "tags" | "topic-relations";
export type FieldSpec = { key: string; label: string; type?: "text" | "textarea" | "url" | "logo-url" | "image-url" | "number" | "datetime" | "boolean" | "lines" | "select" | "reference" | "language"; required?: boolean; max?: number; min?: number; step?: number; choices?: string[]; resource?: Resource; help?: string; tooltip?: string; createOnly?: boolean; default?: unknown };
export type ColumnSpec = { key: string; label: string; sort?: boolean; resource?: Resource; date?: boolean };
export type ResourceSpec = { label: string; singular: string; description: string; group: string; title: string; defaultSort: string; columns: ColumnSpec[]; fields: FieldSpec[]; filter?: { key: string; label: string; choices: string[] }; readonly?: boolean };
const name: FieldSpec = { key: "name", label: "Name", required: true, max: 100 };
const slug: FieldSpec = { key: "slug", label: "Slug", required: true, max: 100, help: "Generated from the name for new records; editable. Lowercase words separated by hyphens, used in URLs and matching." };
const topic: FieldSpec = { key: "topic_id", label: "Topic", type: "reference", resource: "topics", help: "Link to an active topic. This does not add transitive feed matches." };
const aliases: FieldSpec = { key: "aliases", label: "Aliases", type: "lines", help: "One alternative name per line." };
const identityColumns: ColumnSpec[] = [{ key: "name", label: "Name", sort: true }, { key: "slug", label: "Slug", sort: true }];
export const reviewChoices = ["pending", "approved", "rejected"];
export const contentTypes = ["article", "news", "tutorial", "release", "comparison", "opinion"];
export const contentFormats = ["article", "podcast", "video", "paper", "discussion"];
export const relationKinds = ["uses_language", "depends_on", "implements", "part_of", "related_to"];
const job = (label: string, resource: "articles" | "sources"): ResourceSpec => ({ label, singular: "Job", description: "Pipeline run records. Worker-owned state is read-only.", group: "Operations", title: "id", defaultSort: "-created_at", readonly: true, fields: [], columns: [{ key: "id", label: "Run" }, { key: "status", label: "Status", sort: true }, { key: resource === "articles" ? "article_id" : "source_id", label: resource === "articles" ? "Article" : "Source", resource }, { key: "attempts", label: "Attempts" }, { key: "created_at", label: "Created", date: true, sort: true }], filter: { key: "status", label: "Status", choices: ["queued", "running", "succeeded", "failed"] } });

export const resources: Record<Resource, ResourceSpec> = {
  articles: { label: "Articles", singular: "Article", description: "Manage original metadata, classification, review, and publication.", group: "Content", title: "title", defaultSort: "-discovered_at", columns: [{ key: "title", label: "Title", sort: true }, { key: "review_status", label: "Review", sort: true }, { key: "publication_status", label: "Publication", sort: true }, { key: "language", label: "Language" }, { key: "discovered_at", label: "Discovered", date: true, sort: true }], filter: { key: "review_status", label: "Review status", choices: reviewChoices }, fields: [
    { key: "title", label: "Title", required: true, max: 500 },
    { key: "canonical_url", label: "Canonical URL", type: "url", required: true, createOnly: true, max: 2048 },
    { key: "source_id", label: "Source", type: "reference", resource: "sources", required: true, createOnly: true },
    { key: "summary", label: "Original summary", type: "textarea", max: 100000, help: "Source-provided or human-authored text. AI-generated prose is shown separately, never edited into this field automatically." },
    { key: "author", label: "Author", max: 200 }, { key: "image_url", label: "Image URL", type: "image-url", max: 2048 },
    { key: "language", label: "Language", type: "language", max: 35, help: "Select the article’s language, or leave it unspecified if unknown." },
    { key: "content_type", label: "Content type", type: "select", choices: contentTypes, default: "article", required: true },
    { key: "content_format", label: "Format", type: "select", choices: contentFormats, default: "article", required: true },
    { key: "published_at", label: "Original publication date", type: "datetime", help: "Your local time; stored with a timezone." },
  ] },
  sources: { label: "Sources", singular: "Source", description: "RSS and Atom publishers and aggregators, including pending submissions.", group: "Content", title: "name", defaultSort: "name", columns: [{ key: "name", label: "Name", sort: true }, { key: "source_type", label: "Type" }, { key: "approval_status", label: "Review", sort: true }, { key: "enabled", label: "Enabled" }, { key: "last_success_at", label: "Last success", date: true }], filter: { key: "approval_status", label: "Approval", choices: reviewChoices }, fields: [
    { ...name, max: 200, required: false, help: "When creating a source, leave blank to use the RSS title." },
    { key: "feed_url", label: "RSS / Atom URL", type: "url", required: true, createOnly: true, max: 2048, help: "Validated before saving. Existing feed URLs are immutable." },
    { key: "source_type", label: "Source type", type: "select", choices: ["publisher", "aggregator"], required: true, createOnly: true, default: "publisher", tooltip: "Source type is fixed after creation and determines how ingestion finds original article content.", help: "Publisher: original articles. Aggregator: links to articles on other sites." },
    { key: "description", label: "Short description", type: "textarea", max: 500 },
    { key: "website_url", label: "Website", type: "url", max: 2048 }, { key: "logo_url", label: "Logo URL", type: "logo-url", max: 2048, help: "The source’s logo or declared website icon." }, { key: "image_url", label: "Image URL", type: "image-url", max: 2048, help: "A preview or cover image, separate from the logo." }, { key: "language", label: "Language", type: "language", max: 35, help: "The source’s primary language. Article languages are detected separately." },
    { key: "poll_interval_seconds", label: "Poll interval (seconds)", type: "number", required: true, min: 300, max: 604800, default: 1800 },
    { key: "enabled", label: "Enable polling", type: "boolean", default: true },
  ] },
  topics: { label: "Topics", singular: "Topic", description: "The single catalog of developer subjects. Import or discover proposals, then review them before activation.", group: "Taxonomy", title: "name", defaultSort: "name", columns: [identityColumns[0], { key: "kind", label: "Kind", sort: true }, { key: "status", label: "Status", sort: true }, { key: "updated_at", label: "Updated", date: true }], filter: { key: "status", label: "Status", choices: ["proposed", "active", "rejected"] }, fields: [name, slug, { key: "kind", label: "Kind", required: true, max: 50, help: "A descriptive kind, such as discipline, language, framework, or platform." }, { key: "status", label: "Status", type: "select", choices: ["proposed", "active", "rejected"], required: true, default: "active" }, { ...aliases, help: "One alternative name or abbreviation per line. Shared aliases are allowed and help find all matching topics." }, { key: "keywords", label: "Matching keywords", type: "lines", help: "Reviewed terms used by fallback classification. Aliases help search and AI candidate selection." }, { key: "description", label: "Description", type: "textarea", max: 2000 }, { key: "website_url", label: "Website", type: "url", max: 2048 }, { key: "logo_url", label: "Logo URL", type: "logo-url", max: 2048 }] },
  tags: { label: "Tags", singular: "Tag", description: "Dynamic labels with aliases and optional topic links.", group: "Taxonomy", title: "name", defaultSort: "name", columns: [...identityColumns, { key: "topic_id", label: "Topic", resource: "topics" }], fields: [name, slug, aliases, topic] },
  "topic-relations": { label: "Topic relationships", singular: "Topic relationship", description: "Explicit, directional relationships. These do not imply article relevance.", group: "Taxonomy", title: "relation", defaultSort: "relation", columns: [{ key: "topic_id", label: "From topic", resource: "topics" }, { key: "relation", label: "Relationship", sort: true }, { key: "related_topic_id", label: "To topic", resource: "topics" }], fields: [{ key: "topic_id", label: "From topic", type: "reference", resource: "topics", required: true, createOnly: true }, { key: "relation", label: "Relationship", type: "select", choices: relationKinds, required: true, createOnly: true, default: "related_to" }, { key: "related_topic_id", label: "To topic", type: "reference", resource: "topics", required: true, createOnly: true }, { key: "evidence_url", label: "Evidence URL", type: "url", max: 2048 }] },
  "ingestion-jobs": job("Feed ingestion", "sources"),
  "article-jobs": job("Article enrichment", "articles"),
  "image-jobs": job("Image enrichment", "articles"),
  "source-jobs": job("Source enrichment", "sources"),
  "analysis-jobs": { ...job("AI analysis", "articles"), description: "Article analysis, topic enrichment, and relationship research.", columns: [
    { key: "id", label: "Run" }, { key: "kind", label: "Type" }, { key: "target_name", label: "Subject" },
    { key: "status", label: "Status", sort: true }, { key: "attempts", label: "Attempts" }, { key: "created_at", label: "Created", date: true, sort: true },
  ] },
  "notification-jobs": { ...job("Notification delivery", "sources"), description: "Persistent inbox delivery attempts, retries, and runtime logs.", columns: [{ key: "id", label: "Run" }, { key: "status", label: "Status", sort: true }, { key: "attempts", label: "Attempts" }, { key: "created_at", label: "Created", date: true, sort: true }] },
};
export const resourceKeys = Object.keys(resources) as Resource[];
export function isResource(value: string): value is Resource { return Object.hasOwn(resources, value); }
export function humanize(value: string) { return value.replaceAll("_", " ").replace(/^./, char => char.toUpperCase()); }
export { recordHref, resourceHref } from "./routes";
