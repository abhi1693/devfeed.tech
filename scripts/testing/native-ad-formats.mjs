export const adFormats = ["feed", "homepage_sponsorship", "sponsored_content", "engagement"];

export const sponsoredArticle = {
  id: "99999999-9999-4999-8999-999999999999",
  slug: "reliable-api-checks",
  title: "Build reliable API checks",
  canonical_url: "https://publisher.example/reliable-api-checks",
  summary: "A practical guide to checking API reliability in production.",
  ai_summary: null,
  ai_description: null,
  image_url: null,
  author: "Engineering team",
  content_type: "tutorial",
  content_format: "article",
  language: "en",
  published_at: "2026-10-10T10:00:00Z",
  feed_at: "2026-10-10T10:00:00Z",
  tags: [],
  topics: [
    {
      id: "77777777-7777-4777-8777-777777777777",
      name: "Kubernetes",
      slug: "kubernetes",
      kind: "platform",
      role: "primary",
      relevance: 1,
    },
  ],
  sources: [],
  sponsor_names: [],
};

export function creativeProduct(product) {
  return {
    ...product,
    description: "Monitor production APIs and diagnose reliability problems.",
    logo_url: "https://assets.partner.example/logo.svg",
    image_url: "https://assets.partner.example/creative.svg",
  };
}

export async function installCreativeMedia(context) {
  await context.route("https://assets.partner.example/**", (route) =>
    route.fulfill({
      contentType: "image/svg+xml",
      body: route.request().url().endsWith("logo.svg")
        ? '<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48"><rect width="48" height="48" rx="10" fill="#f59e0b"/></svg>'
        : '<svg xmlns="http://www.w3.org/2000/svg" width="600" height="315"><rect width="600" height="315" fill="#1e293b"/><text x="40" y="170" fill="white" font-size="48">API Checker</text></svg>',
    }),
  );
}
