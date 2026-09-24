import { safeExternalUrl } from "./feed-query";
import type { UserIdentity, UserProfile } from "./user";
import brandMark from "@devfeed/theme/assets/devfeed-mark.png";

/** Wrap the entire bio at measured word boundaries, never ellipsizing it. */
export function wrapCardBio(value: string, maxWidth: number, measure: (text: string) => number) {
  const lines: string[] = [];
  let line = "";
  for (const word of value.trim().split(/\s+/u).filter(Boolean)) {
    const candidate = line ? `${line} ${word}` : word;
    if (measure(candidate) <= maxWidth) {
      line = candidate;
      continue;
    }
    if (line) lines.push(line);
    line = "";
    // Long URLs and languages without spaces must wrap too, without shrinking.
    for (const character of Array.from(word)) {
      if (line && measure(line + character) > maxWidth) {
        lines.push(line);
        line = "";
      }
      line += character;
    }
  }
  if (line) lines.push(line);
  return lines;
}

export function cardLines(value: string, width: number, limit: number): string[] {
  const words = value.trim().split(/\s+/u).filter(Boolean);
  const lines: string[] = [];
  let line = "";
  for (const word of words) {
    for (const part of word.match(new RegExp(`.{1,${width}}`, "gu")) ?? []) {
      if (Array.from(`${line} ${part}`.trim()).length > width) {
        lines.push(line);
        line = part;
      } else line = `${line} ${part}`.trim();
    }
  }
  if (line) lines.push(line);
  if (lines.length > limit) {
    return lines.slice(0, limit).map((text, i) =>
      i === limit - 1
        ? `${Array.from(text)
            .slice(0, width - 1)
            .join("")}…`
        : text,
    );
  }
  return lines;
}

/** Explicit allowlist: account identifiers and email never enter the card. */
export function devCardData(
  profile: UserProfile,
  user: Pick<UserIdentity, "name"> & Partial<UserIdentity>,
) {
  const name = profile.display_name?.trim() || user.name?.trim() || "DevFeed reader";
  const words = name.split(/\s+/u);
  const initials = [words[0], ...(words.length > 1 ? [words.at(-1)!] : [])]
    .map((word) => Array.from(word)[0])
    .join("")
    .toUpperCase();
  const stack = (profile.stack ?? []).filter((item) => item.section !== "past").slice(0, 4);
  const streak = profile.reading_streak;
  return {
    name,
    initials,
    username: profile.username || null,
    avatar: safeExternalUrl(profile.avatar_url) ?? null,
    bio: profile.bio?.trim() || "",
    location: profile.location ?? null,
    technologies: stack.map((item) => item.name),
    stats: streak
      ? [
          { label: "DAY STREAK", value: streak.current_days ?? 0 },
          { label: "BEST STREAK", value: streak.longest_days ?? 0 },
          { label: "DAYS READING", value: streak.total_days ?? 0 },
        ]
      : [],
  };
}

export type DevCardData = ReturnType<typeof devCardData>;

function loadImage(src: string, crossOrigin = false): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new Image();
    const timer = window.setTimeout(() => {
      image.onload = image.onerror = null;
      image.src = "";
      reject(new Error("Image timed out"));
    }, 5000);
    image.onload = () => {
      clearTimeout(timer);
      resolve(image);
    };
    image.onerror = () => {
      clearTimeout(timer);
      reject(new Error("Image unavailable"));
    };
    if (crossOrigin) image.crossOrigin = "anonymous";
    image.referrerPolicy = "no-referrer";
    image.src = src;
  });
}

/** Rasterize the exact SVG shown in the modal; no third-party capture service or image proxy. */
export async function devCardPng(svg: SVGSVGElement) {
  const copy = svg.cloneNode(true) as SVGSVGElement;
  // Standalone SVGs cannot inherit app CSS variables. Freeze the current shared
  // theme instead of maintaining a separate palette for exported images.
  const sourceNodes = [svg, ...svg.querySelectorAll("*")];
  const copiedNodes = [copy, ...copy.querySelectorAll("*")];
  sourceNodes.forEach((node, index) => {
    const style = getComputedStyle(node);
    for (const property of ["fill", "stroke", "stop-color", "font-family"]) {
      // Keep local paint-server references local to the exported SVG.
      if (node.getAttribute(property)?.startsWith("url(#")) continue;
      copiedNodes[index].setAttribute(property, style.getPropertyValue(property));
    }
  });
  // The bundled brand mark is trusted local artwork, not a user avatar. Embed it
  // separately so standalone exports also work from chrome-extension:// URLs.
  const brand = copy.querySelector("image[data-brand-mark]");
  if (brand) {
    const image = await loadImage(new URL(brandMark.src, document.baseURI).href);
    const surface = document.createElement("canvas");
    surface.width = surface.height = 96;
    const ctx = surface.getContext("2d");
    if (!ctx) throw new Error("Canvas unavailable");
    ctx.drawImage(image, 0, 0, 96, 96);
    brand.setAttribute("href", surface.toDataURL("image/png"));
  }
  let avatarOmitted = false;
  for (const avatar of copy.querySelectorAll("image[data-avatar]")) {
    try {
      const source = safeExternalUrl(avatar.getAttribute("href"));
      if (!source) throw new Error("Invalid avatar");
      // Loading through Image respects both CORS and the extension's existing img-src policy.
      const image = await loadImage(source, true);
      const surface = document.createElement("canvas");
      surface.width = surface.height = 240;
      const ctx = surface.getContext("2d");
      if (!ctx) throw new Error("Canvas unavailable");
      const size = Math.min(image.naturalWidth, image.naturalHeight);
      ctx.drawImage(
        image,
        (image.naturalWidth - size) / 2,
        (image.naturalHeight - size) / 2,
        size,
        size,
        0,
        0,
        240,
        240,
      );
      avatar.setAttribute("href", surface.toDataURL("image/png"));
    } catch {
      avatar.remove();
      avatarOmitted = true;
    }
  }
  const width = svg.viewBox.baseVal.width * 2;
  const height = svg.viewBox.baseVal.height * 2;
  copy.setAttribute("width", String(width));
  copy.setAttribute("height", String(height));
  const source = new XMLSerializer().serializeToString(copy);
  const image = await loadImage(`data:image/svg+xml;charset=utf-8,${encodeURIComponent(source)}`);
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d");
  if (!context) throw new Error("Canvas unavailable");
  context.drawImage(image, 0, 0);
  const blob = await new Promise<Blob>((resolve, reject) => {
    canvas.toBlob(
      (value) => (value ? resolve(value) : reject(new Error("Export failed"))),
      "image/png",
    );
  });
  return { blob, avatarOmitted };
}
