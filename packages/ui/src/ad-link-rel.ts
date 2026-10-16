export const AD_LINK_REL_OPTIONS = ["nofollow", "sponsored", "ugc", "external"] as const;
export type AdLinkRel = (typeof AD_LINK_REL_OPTIONS)[number];

export function parseAdLinkRel(value: unknown): AdLinkRel[] {
  if (
    !Array.isArray(value) ||
    value.length > AD_LINK_REL_OPTIONS.length ||
    new Set(value).size !== value.length ||
    !value.every((token) => AD_LINK_REL_OPTIONS.includes(token as AdLinkRel))
  )
    throw new Error("Invalid ad link relations");
  return value as AdLinkRel[];
}

/** SEO relations are configurable; outbound tab and referrer protection always apply. */
export function adLinkRel(value?: readonly AdLinkRel[], external = true): string {
  let relations: AdLinkRel[];
  try {
    relations = parseAdLinkRel(value === undefined ? ["nofollow"] : value);
  } catch {
    relations = ["nofollow"];
  }
  return [...relations, ...(external ? ["noopener", "noreferrer"] : [])].join(" ");
}
