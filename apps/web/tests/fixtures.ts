import type { Article, Topic, Source } from "@/lib/types";
export const topic: Topic = {
  id: "topic-1",
  slug: "typescript",
  name: "TypeScript",
  kind: "language",
  description: "Typed JavaScript for application development.",
  ai_description: null,
  website_url: null,
  logo_url: null,
};
export const source: Source = {
  slug: "example-source",
  id: "11111111-1111-4111-8111-111111111111",
  name: "Engineering Journal",
  website_url: "https://example.com",
  logo_url: null,
  description: null,
};
export const article: Article = {
  slug: "a-practical-guide-to-typescript-boundaries-42",
  id: "22222222-2222-4222-8222-222222222222",
  canonical_url: "https://example.com/typescript",
  title: "A practical guide to TypeScript boundaries",
  summary: "Learn how to validate data at the boundaries of your application.",
  ai_summary: "A guide to safer application boundaries.",
  ai_description: null,
  image_url: null,
  author: "An author",
  content_type: "tutorial",
  content_format: "article",
  language: "en",
  published_at: "2026-09-11T00:00:00Z",
  feed_at: "2026-09-11T00:00:00Z",
  tags: ["typescript"],
  sources: [source],
  topics: [topic],
};
