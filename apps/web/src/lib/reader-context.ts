import type { Article } from "./types";

export type ArticleIdentity = Pick<Article, "id" | "slug">;
export type ReaderPromptEligibility = { signup: boolean; mustReads: boolean };

export const readerPromptPolicies = {
  browsing: { signup: true, mustReads: true },
  quiet: { signup: false, mustReads: false },
} as const satisfies Record<string, ReaderPromptEligibility>;
