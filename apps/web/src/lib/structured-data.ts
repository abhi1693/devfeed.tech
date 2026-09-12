import { canonicalUrl, articleDescription, SITE_DESCRIPTION, validDate } from "./metadata";
import { safeExternalUrl } from "./feed-query";
import type { Article } from "./types";

export type StructuredData = { [key: string]: unknown };

export function siteStructuredData(logoPath: string): StructuredData {
  const url = canonicalUrl("/");
  return {
    "@context": "https://schema.org",
    "@graph": [
      {
        "@type": "Organization",
        "@id": `${url}#organization`,
        name: "DevFeed",
        url,
        logo: { "@type": "ImageObject", url: canonicalUrl(logoPath) },
      },
      {
        "@type": "WebSite",
        "@id": `${url}#website`,
        name: "DevFeed",
        url,
        description: SITE_DESCRIPTION,
        inLanguage: "en",
        publisher: { "@id": `${url}#organization` },
      },
    ],
  };
}

type PageItem = { name: string; path: string };
export function collectionStructuredData(
  url: string,
  name: string,
  items: PageItem[],
  { offset = 0, parent }: { offset?: number; parent?: PageItem } = {},
): StructuredData {
  const crumbs = [{ name: "Home", path: "/" }, ...(parent ? [parent] : [])];
  if (url !== canonicalUrl("/")) crumbs.push({ name, path: url });
  return {
    "@context": "https://schema.org",
    "@type": "CollectionPage",
    "@id": `${url}#webpage`,
    url,
    name,
    isPartOf: { "@id": `${canonicalUrl("/")}#website` },
    ...(crumbs.length > 1
      ? {
          breadcrumb: {
            "@type": "BreadcrumbList",
            itemListElement: crumbs.map((item, index) => ({
              "@type": "ListItem",
              position: index + 1,
              name: item.name,
              item: item.path === url ? url : canonicalUrl(item.path),
            })),
          },
        }
      : {}),
    mainEntity: {
      "@type": "ItemList",
      // Only the items rendered on this page, not an invented total for the entire feed.
      itemListElement: items.map((item, index) => ({
        "@type": "ListItem",
        position: offset + index + 1,
        name: item.name,
        url: canonicalUrl(item.path),
      })),
    },
  };
}

export function articleStructuredData(article: Article): StructuredData {
  const url = canonicalUrl(`/articles/${encodeURIComponent(article.slug)}`);
  const original = safeExternalUrl(article.canonical_url);
  const image = safeExternalUrl(article.image_url);
  const source = article.sources[0];
  const published = validDate(article.published_at);
  return {
    "@context": "https://schema.org",
    "@type": "WebPage",
    "@id": `${url}#webpage`,
    url,
    name: article.title,
    description: articleDescription(article),
    isPartOf: { "@id": `${canonicalUrl("/")}#website` },
    // This is a preview of the publisher's article, not an article written by DevFeed.
    mainEntity: {
      "@type": "Article",
      ...(original ? { "@id": original, url: original } : {}),
      headline: article.title,
      ...(published ? { datePublished: published } : {}),
      ...(image ? { image: [image] } : {}),
      ...(article.author ? { author: { "@type": "Person", name: article.author } } : {}),
      ...(source
        ? {
            publisher: {
              "@type": "Organization",
              name: source.name,
              ...(safeExternalUrl(source.website_url)
                ? { url: safeExternalUrl(source.website_url) }
                : {}),
            },
          }
        : {}),
      ...(article.language ? { inLanguage: article.language } : {}),
      ...(article.tags.length ? { keywords: article.tags } : {}),
    },
    breadcrumb: {
      "@type": "BreadcrumbList",
      itemListElement: [
        { "@type": "ListItem", position: 1, name: "Home", item: canonicalUrl("/") },
        { "@type": "ListItem", position: 2, name: article.title, item: url },
      ],
    },
  };
}
