import {
  canonicalUrl,
  articleDescription,
  articlePlainText,
  SITE_DESCRIPTION,
  validDate,
} from "./metadata";
import { articleSource } from "./article-attribution";
import { safeExternalUrl, sourceHref } from "./feed-query";
import type { Article } from "./types";

export type StructuredData = { [key: string]: unknown };

export function siteStructuredData(logo: {
  src: string;
  width: number;
  height: number;
}): StructuredData {
  const url = canonicalUrl("/");
  return {
    "@context": "https://schema.org",
    "@graph": [
      {
        "@type": "Organization",
        "@id": `${url}#organization`,
        name: "DevFeed",
        url,
        description: SITE_DESCRIPTION,
        logo: {
          "@type": "ImageObject",
          "@id": `${url}#logo`,
          url: canonicalUrl(logo.src),
          width: logo.width,
          height: logo.height,
        },
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
export function pageStructuredData(
  url: string,
  name: string,
  { description, parent }: { description?: string; parent?: PageItem } = {},
): StructuredData {
  const crumbs = [{ name: "Home", path: "/" }, ...(parent ? [parent] : [])];
  if (url !== canonicalUrl("/")) crumbs.push({ name, path: url });
  return {
    "@context": "https://schema.org",
    "@type": "WebPage",
    "@id": `${url}#webpage`,
    url,
    name,
    ...(description ? { description } : {}),
    isPartOf: { "@id": `${canonicalUrl("/")}#website` },
    ...(crumbs.length > 1
      ? {
          breadcrumb: {
            "@type": "BreadcrumbList",
            "@id": `${url}#breadcrumb`,
            itemListElement: crumbs.map((item, index) => ({
              "@type": "ListItem",
              position: index + 1,
              name: item.name,
              item: item.path === url ? url : canonicalUrl(item.path),
            })),
          },
        }
      : {}),
  };
}

export function collectionStructuredData(
  url: string,
  name: string,
  items: PageItem[],
  { offset = 0, parent }: { offset?: number; parent?: PageItem } = {},
): StructuredData {
  return {
    ...pageStructuredData(url, name, { parent }),
    "@type": "CollectionPage",
    mainEntity: {
      "@type": "ItemList",
      "@id": `${url}#items`,
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
  const source = articleSource(article);
  const publisher = article.publisher;
  const published = validDate(article.published_at);
  const description = articleDescription(article);
  const originalTitle = article.original_title?.trim();
  const originalDescription = articlePlainText(article.summary).slice(0, 180);
  const overview = article.ai_summary?.trim();
  const authors = (article.authors ?? [])
    .filter((author) => author.name.trim() && ["Person", "Organization"].includes(author.type))
    .map((author) => ({
      "@type": author.type,
      name: author.name.trim(),
      ...(safeExternalUrl(author.url) ? { url: safeExternalUrl(author.url) } : {}),
    }));
  return {
    ...pageStructuredData(url, article.title, {
      description,
      ...(source ? { parent: { name: source.name.trim(), path: sourceHref(source) } } : {}),
    }),
    publisher: { "@id": `${canonicalUrl("/")}#organization` },
    ...(article.tags.length ? { keywords: article.tags } : {}),
    ...(overview
      ? {
          hasPart: {
            "@type": "CreativeWork",
            "@id": `${url}#overview`,
            url: `${url}#article-overview`,
            name: "AI overview",
            text: overview,
            encodingFormat: "text/markdown",
            inLanguage: "en",
            publisher: { "@id": `${canonicalUrl("/")}#organization` },
            ...(original ? { isBasedOn: { "@id": original } } : {}),
          },
        }
      : {}),
    // This is a preview of the publisher's article, not an article written by DevFeed.
    mainEntity: {
      "@type": "Article",
      ...(original ? { "@id": original, url: original } : {}),
      ...(originalTitle ? { headline: originalTitle } : {}),
      ...(originalDescription ? { description: originalDescription } : {}),
      ...(published ? { datePublished: published } : {}),
      ...(image ? { image: [image] } : {}),
      ...(authors.length ? { author: authors } : {}),
      ...(publisher?.name.trim()
        ? {
            publisher: {
              "@type": "Organization",
              name: publisher.name.trim(),
              ...(safeExternalUrl(publisher.website_url)
                ? { url: safeExternalUrl(publisher.website_url) }
                : {}),
            },
          }
        : {}),
      ...(article.language ? { inLanguage: article.language } : {}),
      ...(article.tags.length ? { keywords: article.tags } : {}),
    },
  };
}
