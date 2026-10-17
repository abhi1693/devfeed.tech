"use client";

import { ReaderShare } from "./reader-share";
export { readerShareLinks as articleShareLinks } from "./reader-share";

export function ArticleShare({
  slug,
  title,
  label = false,
  contentFormat,
}: {
  slug: string;
  title: string;
  label?: boolean;
  contentFormat?: string | null;
}) {
  return (
    <ReaderShare
      path={`/articles/${encodeURIComponent(slug)}`}
      title={title}
      kind={contentFormat === "video" ? "video" : "article"}
      label={label}
    />
  );
}
