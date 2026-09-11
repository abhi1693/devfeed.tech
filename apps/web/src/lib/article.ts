import "server-only";
import { cache } from "react";
import { notFound, permanentRedirect } from "next/navigation";
import { getArticle, UserApiError } from "./api";
export const loadArticle = cache(async (slug: string) => {
  if (!/^[a-z0-9][a-z0-9-]{0,199}$/i.test(slug)) notFound();
  const article = await getArticle(slug).catch((error) => {
    if (error instanceof UserApiError && error.status === 404) notFound();
    throw error;
  });
  if (slug !== article.slug) permanentRedirect(`/articles/${article.slug}`);
  return article;
});
