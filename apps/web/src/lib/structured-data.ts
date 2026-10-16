import { isVideo, videoDuration } from "./video";
import { canonicalUrl, articleDescription, SITE_DESCRIPTION, validDate } from "./metadata";
import { safeExternalUrl } from "./feed-query";
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
  const source = article.sources[0];
  const published = validDate(article.published_at);
  const description = articleDescription(article);
  const author = article.author?.trim();
  if (isVideo(article))
    return {
      ...pageStructuredData(url, article.title, { description }),
      mainEntity: {
        "@type": "VideoObject",
        ...(original ? { "@id": original, url: original } : {}),
        name: article.title,
        description,
        ...(published ? { uploadDate: published } : {}),
        ...(image ? { thumbnailUrl: [image] } : {}),
        ...(videoDuration(article.video_metadata?.duration_seconds) !== null
          ? { duration: `PT${article.video_metadata!.duration_seconds}S` }
          : {}),
        ...(article.video_metadata?.channel_title
          ? { creator: { "@type": "Organization", name: article.video_metadata.channel_title } }
          : {}),
        ...(article.language ? { inLanguage: article.language } : {}),
      },
    };
  return {
    ...pageStructuredData(url, article.title, { description }),
    // This is a preview of the publisher's article, not an article written by DevFeed.
    mainEntity: {
      "@type": "Article",
      ...(original ? { "@id": original, url: original } : {}),
      headline: article.title,
      description,
      ...(published ? { datePublished: published } : {}),
      ...(image ? { image: [image] } : {}),
      ...(author ? { author: { "@type": "Person", name: author } } : {}),
      ...(source?.name.trim()
        ? {
            publisher: {
              "@type": "Organization",
              name: source.name.trim(),
              ...(safeExternalUrl(source.website_url)
                ? { url: safeExternalUrl(source.website_url) }
                : {}),
            },
          }
        : {}),
      ...(article.language ? { inLanguage: article.language } : {}),
      ...(article.tags.length ? { keywords: article.tags } : {}),
    },
  };
}
