import "server-only";
import { cardImage } from "./card-image";

/** Deduplicate downloads and share a five-second deadline across four consumers. */
export async function cardLogos(urls: (string | null)[]) {
  const pending = [...new Set(urls.filter((url): url is string => Boolean(url)))];
  const images = new Map<string, string | null>();
  const deadline = Date.now() + 5000;
  let cursor = 0;
  await Promise.all(
    Array.from({ length: Math.min(4, pending.length) }, async () => {
      while (cursor < pending.length) {
        const url = pending[cursor++];
        images.set(url, await cardImage(url, "logo", Math.max(0, deadline - Date.now())));
      }
    }),
  );
  return images;
}
