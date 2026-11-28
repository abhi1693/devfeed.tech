import type { Root } from "mdast";
import type { Plugin } from "unified";

/** One fixed parser configuration per instance; retain syntax, never React/HTML/request state. */
export function createMarkdownParseCache({
  maxEntries = 128,
  maxBytes = 2 * 1024 * 1024,
  maxSourceLength = 16 * 1024,
} = {}) {
  const entries = new Map<string, { tree: string; bytes: number }>();
  let bytes = 0;
  let hits = 0;
  let misses = 0;
  let bypasses = 0;

  const plugin: Plugin<[], Root> = function () {
    const parse = this.parser;
    if (!parse) return;
    this.parser = (source, file) => {
      if (source.length > maxSourceLength) {
        bypasses++;
        return parse(source, file);
      }
      const cached = entries.get(source);
      if (cached) {
        hits++;
        entries.delete(source);
        entries.set(source, cached);
        // Every render owns its tree; a later plugin cannot mutate cached syntax.
        return JSON.parse(cached.tree) as Root;
      }
      misses++;
      const tree = parse(source, file);
      const serialized = JSON.stringify(tree);
      // Two bytes per UTF-16 code unit conservatively bounds retained string payloads.
      const weight = 2 * (source.length + serialized.length);
      if (weight <= maxBytes && maxEntries > 0) {
        while (entries.size >= maxEntries || bytes + weight > maxBytes) {
          const oldest = entries.keys().next().value;
          if (oldest === undefined) break;
          bytes -= entries.get(oldest)!.bytes;
          entries.delete(oldest);
        }
        entries.set(source, { tree: serialized, bytes: weight });
        bytes += weight;
      } else {
        bypasses++;
      }
      return tree;
    };
  };

  return {
    plugin,
    stats: () => ({ entries: entries.size, bytes, hits, misses, bypasses }),
    clear: () => {
      entries.clear();
      bytes = hits = misses = bypasses = 0;
    },
  };
}

export const markdownParseCache = createMarkdownParseCache();
