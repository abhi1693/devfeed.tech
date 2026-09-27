import "server-only";
import { cachedCardImage, cardImage } from "./card-image";

/** Look up already sanitized logos without adding network work to the SVG response. */
export function cachedCardLogos(urls: (string | null)[]) {
  const pending = [...new Set(urls.filter((url): url is string => Boolean(url)))];
  const images = new Map<string, string | null>();
  for (const url of pending) images.set(url, cachedCardImage(url, "logo"));
  return images;
}

/** Warm missing logos in the background with bounded concurrency and a shared deadline. */
export async function warmCardLogos(urls: (string | null)[]) {
  const pending = [...new Set(urls.filter((url): url is string => Boolean(url)))].filter(
    (url) => !cachedCardImage(url, "logo"),
  );
  const deadline = Date.now() + 5000;
  let cursor = 0;
  await Promise.all(
    Array.from({ length: Math.min(4, pending.length) }, async () => {
      while (cursor < pending.length) {
        const url = pending[cursor++];
        await cardImage(url, "logo", Math.max(0, deadline - Date.now()));
      }
    }),
  );
}
