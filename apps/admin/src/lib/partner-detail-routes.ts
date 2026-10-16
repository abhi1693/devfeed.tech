export const partnerDetailSections = {
  products: ["details", "related"],
  pipeline: ["details", "related", "logs"],
  evaluations: ["details", "related", "results", "logs"],
} as const;

export type PartnerDetailResource = keyof typeof partnerDetailSections;

/** Optional sections have the same meaning for rendering, links, and telemetry. */
export function resolvePartnerDetailSection<Resource extends PartnerDetailResource>(
  resource: Resource,
  segments: readonly string[] = [],
): (typeof partnerDetailSections)[Resource][number] | undefined {
  if (segments.length > 1) return;
  return partnerDetailSections[resource].find((section) => section === (segments[0] ?? "details"));
}
