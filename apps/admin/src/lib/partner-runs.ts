export type PartnerRunKind = "pipeline" | "evaluations";
export const partnerRunLabels = { pipeline: "Pipeline jobs", evaluations: "Evaluations" };
export const partnerRunPath = (kind: PartnerRunKind) => `/partnerships/${kind}`;
export const partnerRunHref = (kind: PartnerRunKind, id: string) =>
  `${partnerRunPath(kind)}/${encodeURIComponent(id)}`;
export const partnerOperation = (operation: string) =>
  ({ sync: "Discover products", sync_product: "Sync product", assess: "Product check" })[
    operation
  ] ?? operation;
export const partnerProductHref = (id: string) =>
  `/partnerships/products/${encodeURIComponent(id)}`;
