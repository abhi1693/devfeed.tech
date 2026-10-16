// Match the auto-fill grid: 280px minimum tracks, 12px gaps and 2px card borders.
// Eager images cannot use sizes=auto. Reserve enough pixels for either sidebar state
// without waiting for hydration or replacing a request after measuring the DOM.
function cardImageSizes() {
  const sizes: string[] = [];
  let start = 0;
  for (const { end, outers } of [
    { end: 520, outers: [36] },
    { end: 800, outers: [40] },
    { end: 1200, outers: [128, 264] },
    { end: 3000, outers: [140, 296] },
  ]) {
    const cuts = [
      ...new Set(
        outers.flatMap((outer) =>
          Array.from({ length: 12 }, (_, index) => outer + (index + 2) * 292 - 12),
        ),
      ),
    ]
      .filter((cut) => cut > start && cut <= end)
      .sort((a, b) => a - b);
    for (const boundary of [...cuts, end]) {
      const sample = (start + boundary) / 2;
      const values = outers.map((outer) => {
        const columns = Math.max(1, Math.floor((sample - outer + 12) / 292));
        return `calc((100vw - ${outer + (columns - 1) * 12 + columns * 2}px) / ${columns})`;
      });
      const value = values.length === 1 ? values[0] : `max(${values.join(", ")})`;
      // Use only basic calc arithmetic: the HTML image preloader need not support
      // the newer typed division/round() functions supported by CSS layout.
      sizes.push(
        `${boundary === end ? `(max-width: ${boundary}px)` : `(width < ${boundary}px)`} ${value}`,
      );
      start = boundary;
    }
  }
  return [...sizes, "570px"].join(", ");
}

export const CARD_IMAGE_SIZES = cardImageSizes();

// Use the publisher's image CDN directly; never proxy arbitrary URLs through Next.
export function articleImageSources(src?: string) {
  if (!src) return { src };
  let url: URL;
  try {
    url = new URL(src);
  } catch {
    return { src };
  }
  const asset = url.pathname.match(
    /\/images\/[^/]+\/[^/]+\/[^/]+-(\d+)x\d+\.(?:png|jpe?g|webp|avif)$/i,
  );
  // Leave other hosts and existing transformations/signatures untouched.
  if (url.origin !== "https://cdn.sanity.io" || !asset || url.search || url.hash) return { src };
  const maximum = Math.min(Number(asset[1]), 1920);
  const widths = [
    ...new Set([320, 480, 640, 768, 960, 1280, 1600, maximum].filter((w) => w <= maximum)),
  ];
  const resized = (width: number) => {
    const image = new URL(url);
    image.searchParams.set("w", String(width));
    image.searchParams.set("fit", "max");
    image.searchParams.set("auto", "format");
    image.searchParams.set("q", "75");
    return image.href;
  };
  return {
    src: resized(Math.min(960, maximum)),
    srcSet: widths.map((width) => `${resized(width)} ${width}w`).join(", "),
  };
}
