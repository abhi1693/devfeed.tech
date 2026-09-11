import "server-only";
import { cache } from "react";
import { notFound } from "next/navigation";
import { getArticle, UserApiError } from "./api";
export const loadArticle = cache(async (id: string) => {
  if (!/^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(id)) notFound();
  return getArticle(id).catch((error) => {
    if (error instanceof UserApiError && error.status === 404) notFound();
    throw error;
  });
});
