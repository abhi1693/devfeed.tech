import {
  readerDestinationContract,
  serializeReaderDestination,
} from "@devfeed/http/reader-destinations";

export const legalEmail = "desk.abhimanyu@gmail.com";
export const legalSocialUrl = "https://x.com/abhi16_93";
export const legalPages = readerDestinationContract.routes
  .flatMap((definition) => {
    if (definition.page?.section !== "legal") return [];
    const path = serializeReaderDestination({
      routeId: definition.id,
      parameters: {},
      query: {},
    });
    if (!path) throw new Error("Invalid legal page definition");
    return [{ ...definition.page, routeId: definition.id, path }];
  })
  .sort((left, right) => left.order - right.order);

export type LegalDocument = (typeof legalPages)[number];

export function legalDocument(routeId: string): LegalDocument {
  const document = legalPages.find((page) => page.routeId === routeId);
  if (!document) throw new Error("Unknown legal document");
  return document;
}
