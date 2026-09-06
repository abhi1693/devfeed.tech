/** Language names come from Intl/CLDR, not a product-maintained translation table. */
const names = new Intl.DisplayNames(["en"], { type: "language", fallback: "none", languageDisplay: "standard" });

export function languageName(code: string) {
  try { return names.of(code) ?? code; } catch { return code; }
}

function supportedLanguages() {
  const codes = new Set<string>();
  // Enumerate recognized two-letter languages and canonicalize legacy aliases.
  // Existing three-letter, regional, and script tags are preserved separately.
  for (const first of "abcdefghijklmnopqrstuvwxyz") {
    for (const second of "abcdefghijklmnopqrstuvwxyz") {
      const candidate = first + second;
      if (names.of(candidate)) codes.add(Intl.getCanonicalLocales(candidate)[0].toLowerCase());
    }
  }
  return [...codes].map(value => ({ value, label: languageName(value) }))
    .sort((a, b) => a.label.localeCompare(b.label, "en"));
}

export const languages = supportedLanguages();
const regionalCodes = ["en-us", "en-gb", "en-in", "es-es", "es-mx", "fr-fr", "fr-ca", "pt-br", "pt-pt", "zh-hans", "zh-hant"];

export function regionalLanguages(current: string) {
  const codes = new Set(regionalCodes);
  if (current && !languages.some(option => option.value === current)) codes.add(current);
  return [...codes].map(value => ({ value, label: languageName(value) }))
    .sort((a, b) => a.label.localeCompare(b.label, "en"));
}
