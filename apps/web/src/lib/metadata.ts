import type { Metadata } from "next";
import type { SearchParams } from "./feed-query";

export function feedMetadata(
  title: string,
  description: string,
  query: SearchParams,
): Metadata {
  const refined = Object.values(query).some((value) =>
    Array.isArray(value) ? value.some(Boolean) : Boolean(value),
  );
  return {
    title,
    description,
    ...(refined ? { robots: { index: false, follow: true } } : {}),
  };
}
