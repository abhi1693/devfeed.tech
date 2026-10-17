"use client";

import { ReaderShare } from "./reader-share";
export { readerShareLinks as articleShareLinks } from "./reader-share";

export function ArticleShare({
  slug,
  title,
  label = false,
}: {
  slug: string;
  title: string;
  label?: boolean;
}) {
  return (
    <ReaderShare
      path={`/articles/${encodeURIComponent(slug)}`}
      title={title}
      kind="article"
      label={label}
    />
  );
}
