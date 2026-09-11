export type Source = {
  id: string;
  name: string;
  website_url: string | null;
  logo_url: string | null;
  description: string | null;
};
export type Topic = {
  id: string;
  name: string;
  slug: string;
  kind: string;
  description: string | null;
  ai_description: string | null;
  logo_url: string | null;
  website_url: string | null;
};
export type Article = {
  id: string;
  canonical_url: string;
  title: string;
  summary: string;
  ai_summary: string | null;
  ai_description: string | null;
  image_url: string | null;
  author: string | null;
  content_type: string;
  content_format: string | null;
  language: string | null;
  published_at: string | null;
  feed_at: string;
  tags: string[];
  sources: Source[];
  topics: (Pick<Topic, "id" | "name" | "slug" | "kind"> & { role?: string })[];
};
export type FeedPage = { items: Article[]; next_cursor: string | null };
