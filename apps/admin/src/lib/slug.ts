/** Match the API's lowercase ASCII, hyphen-separated slug format. */
export function slugify(name: string, maxLength = 100): string {
  return name
    .normalize("NFKD")
    .replace(/\p{M}/gu, "")
    .toLowerCase()
    .replace(/['’]/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, maxLength)
    .replace(/-+$/g, "");
}

/** Feed-style markers are presentation; language punctuation is part of identity. */
export function tagSlugify(value: string, maxLength = 100): string {
  const quotes: Record<string, string> = { '"': '"', "'": "'", "`": "`", "“": "”", "‘": "’" };
  let name = value.normalize("NFKC").trim();
  while (name) {
    const previous = name;
    if (name.length > 1 && quotes[name[0]] === name.at(-1)) name = name.slice(1, -1).trim();
    name = name.replace(/^#+/, "").trim();
    if (name === previous) break;
  }
  return slugify(name.replaceAll("+", " plus ").replaceAll("#", " sharp "), maxLength);
}
