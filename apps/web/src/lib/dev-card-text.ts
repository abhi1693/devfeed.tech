import glyphs from "./dev-card-font-metrics.json";

export type CardFont = { size: number; weight?: number; spacing?: number };
export const cardNameFont: CardFont = { size: 44, weight: 800, spacing: -1.2 };
export const cardBioFont: CardFont = { size: 17 };
const metrics: Record<string, number[]> = glyphs;
const segmenter = new Intl.Segmenter("en", { granularity: "grapheme" });
const characters = (value: string) =>
  Array.from(segmenter.segment(value), ({ segment }) => segment);
const widths = new Map<string, { advance: number; count: number }>();
const cacheLimit = 1024;

/** Shared Arial-compatible advances; cache is bounded even on long-lived servers. */
export function measureCardText(value: string, { size, weight = 400, spacing = 0 }: CardFont) {
  const normalized = value.normalize("NFC");
  const bold = weight >= 600 ? 1 : 0;
  const key = JSON.stringify([normalized, bold]);
  let cached = widths.get(key);
  if (!cached) {
    const parts = characters(normalized);
    const advance = parts.reduce(
      (total, character) =>
        total +
        (metrics[character]?.[bold] ??
          metrics[character.normalize("NFD").replace(/\p{Mark}/gu, "")]?.[bold] ??
          (/\p{Extended_Pictographic}|\p{Regional_Indicator}/u.test(character) ? 2000 : 1050)),
      0,
    );
    cached = { advance, count: parts.length };
    if (widths.size >= cacheLimit) widths.delete(widths.keys().next().value!);
    // Do not retain arbitrary-size strings in a process-wide cache.
    if (normalized.length <= 2048) widths.set(key, cached);
  }
  return Math.max(0, (cached.advance * size) / 1000 + cached.count * spacing);
}

/** Longest fitting prefix in logarithmic measurement calls, without splitting graphemes. */
export function fittingPrefix(
  parts: string[],
  width: number,
  measure: (text: string) => number,
  suffix = "",
) {
  let low = 0;
  let high = parts.length;
  while (low < high) {
    const middle = Math.ceil((low + high) / 2);
    if (measure(parts.slice(0, middle).join("") + suffix) <= width) low = middle;
    else high = middle - 1;
  }
  return low;
}

/** Shared word wrapping. Bios preserve all text; names can cap lines with an ellipsis. */
export function wrapCardText(
  value: string,
  maxWidth: number,
  measure: (text: string) => number,
  limit = Infinity,
) {
  let remaining = characters(value.trim().replace(/\s+/gu, " ").normalize("NFC"));
  const lines: string[] = [];
  while (remaining.length && lines.length < limit) {
    const text = remaining.join("");
    if (measure(text) <= maxWidth) {
      lines.push(text);
      break;
    }
    const last = lines.length === limit - 1;
    let count = fittingPrefix(remaining, maxWidth, measure, last ? "…" : "");
    if (!last) {
      const boundary = remaining.slice(0, count + 1).lastIndexOf(" ");
      if (boundary > 0) count = boundary;
    }
    if (last) {
      lines.push(remaining.slice(0, count).join("").trimEnd() + "…");
      break;
    }
    // A single oversized grapheme still makes progress without dropping bio content.
    count = Math.max(1, count);
    lines.push(remaining.slice(0, count).join("").trim());
    remaining = remaining.slice(count);
    while (remaining[0] === " ") remaining = remaining.slice(1);
  }
  return lines;
}

export function cardTextLayout(name: string, bio: string) {
  return {
    names: wrapCardText(name, 478, (text) => measureCardText(text, cardNameFont), 2),
    bios: wrapCardText(bio, 478, (text) => measureCardText(text, cardBioFont)),
  };
}

export function fitCardText(value: string, font: CardFont, maxWidth: number, minSize = 0) {
  let size = font.size;
  const measure = (text: string, at = size) => measureCardText(text, { ...font, size: at });
  if (measure(value) > maxWidth) {
    // Letter spacing is absolute, so proportional font scaling alone is not enough.
    let low = Math.min(minSize, size);
    let high = size;
    for (let step = 0; step < 16; step++) {
      const middle = (low + high) / 2;
      if (measure(value, middle) <= maxWidth) low = middle;
      else high = middle;
    }
    size = low;
  }
  let text = value;
  if (measure(text) > maxWidth) {
    const parts = characters(text);
    text =
      parts
        .slice(
          0,
          fittingPrefix(parts, maxWidth, (part) => measure(part), "…"),
        )
        .join("") + "…";
  }
  return { text, size, width: measure(text) };
}
