import "server-only";
import { createLlmsTxtHandler } from "@dualmark/nextjs";
import { aiUnavailable, conditionalResponse, renderPublicMarkdown } from "./ai-content";
import { publicSiteOrigin } from "./config";

export function llmsIndex(request: Request) {
  const origin = publicSiteOrigin();
  const response = createLlmsTxtHandler({
    brandName: "DevFeed",
    description:
      "Developer news, tutorials, articles, releases, comparisons and opinions organized by topic, source and tag. Public previews include original publisher links and clearly labeled AI summaries. DevFeed does not host the publishers' full articles.",
    cacheControl: "public, max-age=3600, s-maxage=3600",
    sections: [
      {
        title: "Start here",
        links: [
          {
            title: "Full agent guide and current public content",
            href: `${origin}/llms-full.txt`,
            description:
              "Expanded reading guide and the first page of each public directory and feed; follow pagination for the archive.",
          },
          {
            title: "Latest published content",
            href: `${origin}/index.md`,
            description: "Article previews with cursor pagination.",
          },
          {
            title: "Search",
            href: `${origin}/search.md?q=kubernetes`,
            description:
              "Search articles first, followed by topics, sources and tags. Replace q with your query.",
          },
        ],
      },
      {
        title: "Directories",
        links: [
          {
            title: "Topics",
            href: `${origin}/topics.md`,
            description: "Approved topics with published articles.",
          },
          {
            title: "Sources",
            href: `${origin}/sources.md`,
            description: "Enabled, approved sources with published articles.",
          },
          {
            title: "Tags",
            href: `${origin}/tags.md`,
            description: "Tags attached to published articles; a Markdown directory.",
          },
        ],
      },
      {
        title: "Content types",
        links: ["articles", "news", "tutorials", "releases", "comparisons", "opinions"].map(
          (type) => ({ title: type, href: `${origin}/${type}.md` }),
        ),
      },
      {
        title: "Complete URL discovery",
        links: [
          {
            title: "Sitemap index",
            href: `${origin}/sitemap.xml`,
            description:
              "Versioned, cached inventories of public article, topic, tag and source URLs. Append .md to an item URL for its readable representation.",
          },
          { title: "Robots", href: `${origin}/robots.txt`, description: "Crawler rules." },
        ],
      },
    ],
  }).GET();
  return response
    .text()
    .then((body) =>
      conditionalResponse(request, new Response(body, { headers: response.headers }), body),
    );
}

function guide() {
  const origin = publicSiteOrigin();
  return `# DevFeed — Full agent guide

> DevFeed helps developers discover published technical content by article, topic, source and tag. This file expands the discovery guide and includes current public previews and directory entries. It is not a dump of the entire article archive. Every included collection is bounded and links to its next page.

Website: ${origin}
Discovery index: ${origin}/llms.txt
Complete URL inventory: ${origin}/sitemap.xml

## Reading public content

Request an existing public page with \`Accept: text/markdown\`, or append \`.md\` to its path. The homepage twin is \`/index.md\`. Normal browser requests continue to receive HTML. HTML pages advertise their Markdown twin with a Link response header. Known AI agents may receive Markdown when their Accept header permits it; an explicit HTML preference is respected.

- Article preview: \`/articles/{slug}.md\`
- Topic and its articles: \`/topics/{slug}.md\`
- Source and its articles: \`/sources/{slug}.md\`
- Tag and its articles: \`/tags/{slug}.md\`
- Content types: \`/articles.md\`, \`/news.md\`, \`/tutorials.md\`, \`/releases.md\`, \`/comparisons.md\`, \`/opinions.md\`
- A topic, source or tag can also be narrowed by a content-type suffix, for example \`/topics/{slug}/tutorials.md\`.

Use the actual slugs and UUIDs returned in links; percent-encode path segments. Do not invent record identifiers.

## Searching and pagination

Search with \`/search.md?q={encoded-query}\`. Results are grouped into articles, topics, sources and tags, with articles first. Follow the section's More link to continue. Queries are limited to 200 characters.

Feeds return up to 24 previews per page and use an opaque \`cursor\`. Follow the Next page URL without editing the cursor. Topic, source and tag directories return up to 60 entries per page and use \`offset\`. A final empty directory page is possible. Absence from the first page does not mean an item is absent from DevFeed.

Feed filters include \`content_type\` (article, news, tutorial, release, comparison, opinion), \`topic\` (slug), \`tag\` (slug), \`source_id\` (UUID) and \`language\` (language code). Keep these filters when continuing pagination. Use the dedicated search page for cross-collection keyword lookup.

For complete discovery, follow each child sitemap in the sitemap index. Preserve its version query parameter while reading the inventory. Append .md to the discovered content URLs, not to the XML filenames. The inventory refreshes periodically and may lag recent moderation changes; a missing content page returns 404.

## Meaning and attribution

An article here is a public preview, not a copy of the complete original article. The original publisher link identifies where to read and cite the source. AI overview sections are generated summaries; source excerpts are labeled separately. Verify claims against the original publisher when accuracy matters. Topics organize approved subject areas; tags are labels and are not endorsements.

Dates are exposed in the API's timestamp format. Published uses the publisher's publication timestamp where available, otherwise DevFeed's feed timestamp. Content type describes the editorial format. Source and topic links point to related public entries.

## Access and freshness

These representations require no sign-in and never include account settings, followed items, personalized feeds, notifications, admin data or unpublished content. Account actions still require the normal signed-in application. Fetching a Markdown preview does not record an original-article click.

Content reads use the public API's shared Redis/Valkey response cache, including its publication invalidations. Markdown responses require revalidation and expose an ETag; send If-None-Match to receive 304 when unchanged. The static discovery index can be cached for one hour. A temporary dependency outage returns 503 and Retry-After; it is not an empty archive. A 400 or 422 indicates invalid parameters, and 404 indicates an unavailable public page.

## Current public content

The following sections are the first pages fetched for this response. They are separate paginated views, not one transactional archive snapshot.
`;
}

export async function llmsFull(request: Request) {
  try {
    const sections = await Promise.all(
      ["/", "/topics", "/sources", "/tags"].map((path) => renderPublicMarkdown(path)),
    );
    const body = [guide(), ...sections].join("\n\n---\n\n");
    return conditionalResponse(
      request,
      new Response(body, {
        headers: {
          "Content-Type": "text/plain; charset=utf-8",
          "Cache-Control": "public, max-age=0, must-revalidate",
          "X-Robots-Tag": "noindex",
        },
      }),
      body,
    );
  } catch (error) {
    return aiUnavailable(error);
  }
}
