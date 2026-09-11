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
  if (
    url.origin !== "https://cdn.sanity.io" ||
    !asset ||
    url.search ||
    url.hash
  )
    return { src };
  const maximum = Math.min(Number(asset[1]), 1920);
  const widths = [
    ...new Set(
      [320, 480, 640, 768, 960, 1280, 1600, maximum].filter(
        (w) => w <= maximum,
      ),
    ),
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
