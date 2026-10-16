/** UI descriptions, not domain data: topic/tag choices always come from the API. */
import type { ColumnPresentation } from "./column-kinds";
import type { SimpleResource } from "./record-identities";
export type Resource =
  | "users"
  | "articles"
  | "sources"
  | "topics"
  | "tags"
  | "topic-relations"
  | "ingestion-jobs"
  | "article-jobs"
  | "image-jobs"
  | "source-jobs"
  | "analysis-jobs"
  | "notification-jobs";
export type ContentResource = "articles" | "sources" | "topics" | "tags" | "topic-relations";
export type RouteGroup = "content" | "taxonomy" | "jobs" | "enrichment";
type Breadcrumb = { label: string; href: string };
export type ResourceNavigation = {
  href: string;
  groups: readonly RouteGroup[];
  breadcrumbs: readonly Breadcrumb[];
};
type ResourceGroupDefinition = Breadcrumb & { breadcrumbs: readonly Breadcrumb[] };

const contentGroup: ResourceGroupDefinition = {
  label: "Content",
  href: "/content",
  breadcrumbs: [],
};
const taxonomyGroup: ResourceGroupDefinition = {
  label: "Taxonomy",
  href: "/taxonomy",
  breadcrumbs: [],
};
const jobsGroup: ResourceGroupDefinition = { label: "Jobs", href: "/jobs", breadcrumbs: [] };
const enrichmentGroup: ResourceGroupDefinition = {
  label: "Enrichment jobs",
  href: "/jobs/enrichment",
  breadcrumbs: [jobsGroup],
};
export const resourceGroups: Record<RouteGroup, ResourceGroupDefinition> = {
  content: contentGroup,
  taxonomy: taxonomyGroup,
  jobs: jobsGroup,
  enrichment: enrichmentGroup,
};

const contentNavigation = {
  groups: ["content"],
  breadcrumbs: [contentGroup],
} satisfies Omit<ResourceNavigation, "href">;
const taxonomyNavigation = {
  groups: ["taxonomy"],
  breadcrumbs: [taxonomyGroup],
} satisfies Omit<ResourceNavigation, "href">;
const jobsNavigation = {
  groups: ["jobs"],
  breadcrumbs: [jobsGroup],
} satisfies Omit<ResourceNavigation, "href">;
const enrichmentNavigation = {
  groups: ["jobs", "enrichment"],
  breadcrumbs: [jobsGroup, enrichmentGroup],
} satisfies Omit<ResourceNavigation, "href">;
export type FieldSpec = {
  key: string;
  label: string;
  type?:
    | "text"
    | "textarea"
    | "url"
    | "logo-url"
    | "image-url"
    | "number"
    | "datetime"
    | "boolean"
    | "lines"
    | "select"
    | "reference"
    | "language";
  required?: boolean;
  max?: number;
  min?: number;
  step?: number;
  choices?: string[];
  resource?: SimpleResource;
  help?: string;
  tooltip?: string;
  createOnly?: boolean;
  default?: unknown;
};
export type ColumnSpec = ColumnPresentation & {
  key: string;
  label: string;
  sort?: boolean;
  resource?: SimpleResource;
};
export type ResourceSpec = {
  label: string;
  singular: string;
  description: string;
  sidebarGroup: "Content" | "Taxonomy" | "Audience" | "Operations";
  navigation: ResourceNavigation;
  title: string;
  defaultSort: string;
  columns: ColumnSpec[];
  fields: FieldSpec[];
  filter?: { key: string; label: string; choices: string[] };
  readonly?: boolean;
};
const name: FieldSpec = { key: "name", label: "Name", required: true, max: 100 };
const slug: FieldSpec = {
  key: "slug",
  label: "Slug",
  required: true,
  max: 100,
  help: "Use lowercase words separated by hyphens.",
};
const topic: FieldSpec = {
  key: "topic_id",
  label: "Topic",
  type: "reference",
  resource: "topics",
  help: "Choose a topic.",
};
const aliases: FieldSpec = {
  key: "aliases",
  label: "Aliases",
  type: "lines",
  help: "One alternative name per line.",
};
const identityColumns: ColumnSpec[] = [
  { key: "name", label: "Name", kind: "name", sort: true },
  { key: "slug", label: "Slug", kind: "slug", sort: true },
];
export const reviewChoices = ["pending", "approved", "rejected"];
export const contentTypes = ["article", "news", "tutorial", "release", "comparison", "opinion"];
export const contentFormats = ["article", "podcast", "video", "paper", "discussion"];
export const relationKinds = ["uses_language", "depends_on", "implements", "part_of", "related_to"];
const job = (
  label: string,
  resource: "articles" | "sources",
  navigation: ResourceNavigation,
): ResourceSpec => ({
  label,
  singular: "Job",
  description: "View runs and retry failed jobs.",
  sidebarGroup: "Operations",
  navigation,
  title: "id",
  defaultSort: "-created_at",
  readonly: true,
  fields: [],
  columns: [
    { key: "id", label: "Run" },
    { key: "status", label: "Status", kind: "pill", sort: true },
    {
      key: resource === "articles" ? "article_id" : "source_id",
      label: resource === "articles" ? "Article" : "Source",
      resource,
    },
    { key: "attempts", label: "Attempts", kind: "number" },
    { key: "created_at", label: "Created", kind: "datetime", sort: true },
  ],
  filter: {
    key: "status",
    label: "Status",
    choices: ["queued", "running", "succeeded", "failed", "retried"],
  },
});

export const resources: Record<Resource, ResourceSpec> = {
  users: {
    label: "Users",
    singular: "User",
    description: "Inspect accounts, profiles, reading activity, and personalized recommendations.",
    sidebarGroup: "Audience",
    navigation: { href: "/users", groups: [], breadcrumbs: [] },
    title: "name",
    defaultSort: "-created_at",
    readonly: true,
    columns: [
      { key: "name", label: "Name", kind: "name", sort: true },
      { key: "username", label: "Username", sort: true },
      { key: "email", label: "Email", sort: true },
      { key: "followed_topics", label: "Topics", kind: "number" },
      { key: "followed_sources", label: "Sources", kind: "number" },
      { key: "liked_articles", label: "Likes", kind: "number" },
      { key: "bookmarks", label: "Bookmarks", kind: "number" },
      { key: "last_read_at", label: "Last article click", kind: "datetime" },
      { key: "created_at", label: "Joined", kind: "datetime", sort: true },
      { key: "last_seen_at", label: "Last active", kind: "datetime", sort: true },
    ],
    filter: { key: "interests", label: "Interests", choices: ["following", "liked", "none"] },
    fields: [
      { key: "name", label: "Display name" },
      { key: "sign_in_name", label: "Sign-in name" },
      { key: "email", label: "Email" },
      { key: "avatar_url", label: "Avatar", type: "logo-url" },
    ],
  },
  articles: {
    label: "Articles",
    singular: "Article",
    description: "Manage articles, reviews, and publication.",
    sidebarGroup: "Content",
    navigation: { href: "/content/articles", ...contentNavigation },
    title: "title",
    defaultSort: "-discovered_at",
    columns: [
      { key: "title", label: "Title", sort: true },
      { key: "review_status", label: "Review", kind: "pill", sort: true },
      { key: "publication_status", label: "Publication", kind: "pill", sort: true },
      { key: "language", label: "Language", kind: "language" },
      { key: "discovered_at", label: "Discovered", kind: "datetime", sort: true },
    ],
    filter: { key: "review_status", label: "Review status", choices: reviewChoices },
    fields: [
      { key: "title", label: "Title", required: true, max: 500 },
      {
        key: "canonical_url",
        label: "Canonical URL",
        type: "url",
        required: true,
        createOnly: true,
        max: 2048,
      },
      {
        key: "source_id",
        label: "Source",
        type: "reference",
        resource: "sources",
        required: true,
        createOnly: true,
      },
      {
        key: "summary",
        label: "Original summary",
        type: "textarea",
        max: 100000,
        help: "Original article description.",
      },
      { key: "author", label: "Author", max: 200 },
      { key: "image_url", label: "Image URL", type: "image-url", max: 2048 },
      {
        key: "language",
        label: "Language",
        type: "language",
        max: 35,
        help: "Select the article’s language, or leave it unspecified if unknown.",
      },
      {
        key: "content_type",
        label: "Content type",
        type: "select",
        choices: contentTypes,
        default: "article",
        required: true,
      },
      {
        key: "content_format",
        label: "Format",
        type: "select",
        choices: contentFormats,
        default: "article",
        required: true,
      },
      {
        key: "published_at",
        label: "Original publication date",
        type: "datetime",
        help: "Your local time.",
      },
    ],
  },
  sources: {
    label: "Sources",
    singular: "Source",
    description: "Manage sources and review submissions.",
    sidebarGroup: "Content",
    navigation: { href: "/content/sources", ...contentNavigation },
    title: "name",
    defaultSort: "name",
    columns: [
      { key: "name", label: "Name", kind: "name", sort: true },
      { key: "source_type", label: "Type", kind: "label" },
      { key: "provider", label: "Provider", kind: "label" },
      { key: "resource_kind", label: "Collection", kind: "label" },
      { key: "approval_status", label: "Review", kind: "pill", sort: true },
      { key: "enabled", label: "Enabled", kind: "boolean" },
      { key: "last_success_at", label: "Last success", kind: "datetime" },
    ],
    filter: { key: "approval_status", label: "Approval", choices: reviewChoices },
    fields: [
      {
        ...name,
        max: 200,
        required: false,
        help: "Leave blank to use the feed title.",
      },
      {
        key: "feed_url",
        label: "Feed or YouTube URL",
        type: "text",
        required: false,
        createOnly: true,
        max: 2048,
        help: "Enter RSS, Atom, YouTube @handle, channel, playlist, or video URL; or provide a website below.",
      },
      {
        key: "source_type",
        label: "Source type",
        type: "select",
        choices: ["publisher", "aggregator"],
        required: true,
        createOnly: true,
        default: "publisher",
        tooltip: "Source type cannot be changed after creation.",
        help: "Publisher: original articles. Aggregator: links to articles on other sites.",
      },
      {
        key: "youtube_target",
        label: "YouTube target",
        type: "select",
        choices: ["video", "playlist", "channel"],
        createOnly: true,
        help: "A single video does not subscribe to all channel uploads.",
      },
      { key: "description", label: "Short description", type: "textarea", max: 500 },
      { key: "website_url", label: "Website", type: "url", max: 2048 },
      {
        key: "logo_url",
        label: "Logo URL",
        type: "logo-url",
        max: 2048,
        help: "The source’s logo or declared website icon.",
      },
      {
        key: "image_url",
        label: "Image URL",
        type: "image-url",
        max: 2048,
        help: "A preview or cover image, separate from the logo.",
      },
      {
        key: "language",
        label: "Language",
        type: "language",
        max: 35,
        help: "The source’s primary language.",
      },
      {
        key: "poll_interval_seconds",
        label: "Poll interval (seconds)",
        type: "number",
        required: true,
        min: 300,
        max: 604800,
        default: 43200,
      },
      { key: "enabled", label: "Enable polling", type: "boolean", default: true },
    ],
  },
  topics: {
    label: "Topics",
    singular: "Topic",
    description: "Manage topics and review suggestions.",
    sidebarGroup: "Taxonomy",
    navigation: { href: "/taxonomy/topics", ...taxonomyNavigation },
    title: "name",
    defaultSort: "name",
    columns: [
      identityColumns[0],
      { key: "kind", label: "Kind", kind: "label", sort: true },
      { key: "status", label: "Status", kind: "pill", sort: true },
      { key: "updated_at", label: "Updated", kind: "datetime" },
    ],
    filter: {
      key: "status",
      label: "Status",
      choices: ["proposed", "active", "rejected"],
    },
    fields: [
      name,
      slug,
      {
        key: "kind",
        label: "Kind",
        required: true,
        max: 50,
        help: "A descriptive kind, such as discipline, language, framework, or platform.",
      },
      {
        key: "status",
        label: "Status",
        type: "select",
        choices: ["proposed", "active", "rejected"],
        required: true,
        default: "active",
      },
      {
        ...aliases,
        help: "One alternative name or abbreviation per line.",
      },
      {
        key: "keywords",
        label: "Matching keywords",
        type: "lines",
        help: "Terms associated with this topic.",
      },
      {
        key: "description",
        label: "Description",
        type: "textarea",
        max: 2000,
        help: "Write a short description in plain text.",
      },
      { key: "website_url", label: "Website", type: "url", max: 2048 },
      {
        key: "logo_url",
        label: "Logo URL",
        type: "logo-url",
        max: 2048,
        help: "SVG and raster logos are processed into optimized images after saving.",
      },
    ],
  },
  tags: {
    label: "Tags",
    singular: "Tag",
    description: "Manage tags and their topics.",
    sidebarGroup: "Taxonomy",
    navigation: { href: "/taxonomy/tags", ...taxonomyNavigation },
    title: "name",
    defaultSort: "name",
    columns: [
      ...identityColumns,
      { key: "topic_id", label: "Topic", resource: "topics" },
      { key: "topic_match_status", label: "Topic discovery", kind: "pill" },
    ],
    fields: [
      name,
      slug,
      aliases,
      {
        key: "auto_link_topic",
        label: "Discover topic automatically",
        type: "boolean",
        default: true,
        help: "Turn off to choose the topic yourself.",
      },
      topic,
    ],
  },
  "topic-relations": {
    label: "Topic relationships",
    singular: "Topic relationship",
    description: "Approved relationships and AI suggestions awaiting review.",
    sidebarGroup: "Taxonomy",
    navigation: { href: "/taxonomy/relationships", ...taxonomyNavigation },
    title: "relation",
    defaultSort: "relation",
    columns: [
      { key: "topic_id", label: "From topic", resource: "topics" },
      { key: "relation", label: "Relationship", sort: true },
      { key: "related_topic_id", label: "To topic", resource: "topics" },
      { key: "status", label: "Status", kind: "pill", sort: true },
      { key: "evidence_url", label: "Evidence" },
    ],
    filter: { key: "status", label: "Status", choices: ["pending", "approved"] },
    fields: [
      {
        key: "topic_id",
        label: "From topic",
        type: "reference",
        resource: "topics",
        required: true,
        createOnly: true,
      },
      {
        key: "relation",
        label: "Relationship",
        type: "select",
        choices: relationKinds,
        required: true,
        createOnly: true,
        default: "related_to",
      },
      {
        key: "related_topic_id",
        label: "To topic",
        type: "reference",
        resource: "topics",
        required: true,
        createOnly: true,
      },
      { key: "evidence_url", label: "Evidence URL", type: "url", max: 2048 },
    ],
  },
  "ingestion-jobs": job("Feed ingestion", "sources", {
    href: "/jobs/ingestion",
    ...jobsNavigation,
  }),
  "article-jobs": job("Article enrichment", "articles", {
    href: "/jobs/enrichment/articles",
    ...enrichmentNavigation,
  }),
  "image-jobs": {
    ...job("Images", "articles", {
      href: "/jobs/enrichment/images",
      ...enrichmentNavigation,
    }),
    columns: [
      { key: "id", label: "Run" },
      { key: "status", label: "Status", kind: "pill", sort: true },
      { key: "operation", label: "Operation" },
      { key: "target_name", label: "Subject" },
      { key: "attempts", label: "Attempts", kind: "number" },
      { key: "created_at", label: "Created", kind: "datetime", sort: true },
    ],
  },
  "source-jobs": job("Source enrichment", "sources", {
    href: "/jobs/enrichment/sources",
    ...enrichmentNavigation,
  }),
  "analysis-jobs": {
    ...job("AI analysis", "articles", {
      href: "/jobs/analysis",
      ...jobsNavigation,
    }),
    description: "Article analysis, topic enrichment, and relationship research.",
    columns: [
      { key: "id", label: "Run" },
      { key: "kind", label: "Type", kind: "label" },
      { key: "target_name", label: "Subject" },
      { key: "status", label: "Status", kind: "pill", sort: true },
      { key: "attempts", label: "Attempts", kind: "number" },
      { key: "created_at", label: "Created", kind: "datetime", sort: true },
    ],
  },
  "notification-jobs": {
    ...job("Notification delivery", "sources", {
      href: "/jobs/notifications",
      ...jobsNavigation,
    }),
    description: "View notification deliveries and retry failures.",
    columns: [
      { key: "id", label: "Run" },
      { key: "status", label: "Status", kind: "pill", sort: true },
      { key: "attempts", label: "Attempts", kind: "number" },
      { key: "created_at", label: "Created", kind: "datetime", sort: true },
    ],
  },
};
export const resourceKeys = Object.keys(resources) as Resource[];
export function humanize(value: string) {
  return value.replaceAll("_", " ").replace(/^./, (char) => char.toUpperCase());
}
