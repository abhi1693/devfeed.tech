function markdownLabels(value: string): string {
  const parts: string[] = [];
  let cursor = 0;
  let search = 0;
  while (search < value.length) {
    const open = value.indexOf("[", search);
    if (open < 0) break;
    const close = value.indexOf("]", open + 1);
    if (close < 0) break;
    if (value[close + 1] !== "(") {
      // Every opening bracket before this closing bracket has the same failed suffix.
      search = close + 1;
      continue;
    }
    const end = value.indexOf(")", close + 2);
    if (end < 0) break;
    const start = value[open - 1] === "!" ? open - 1 : open;
    parts.push(value.slice(cursor, start), value.slice(open + 1, close));
    cursor = end + 1;
    search = cursor;
  }
  parts.push(value.slice(cursor));
  return parts.join("");
}

function withoutTags(value: string): string {
  const parts: string[] = [];
  let cursor = 0;
  while (cursor < value.length) {
    const open = value.indexOf("<", cursor);
    if (open < 0) break;
    const close = value.indexOf(">", open + 1);
    if (close < 0) break;
    parts.push(value.slice(cursor, open), " ");
    cursor = close + 1;
  }
  parts.push(value.slice(cursor));
  return parts.join("");
}

export function articlePlainText(value: string): string {
  return withoutTags(markdownLabels(value))
    .replace(/(^|\n)\s{0,3}(?:#{1,6}\s+|>\s*|[-*+]\s+)/g, "$1")
    .replace(/[*_`~]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}
