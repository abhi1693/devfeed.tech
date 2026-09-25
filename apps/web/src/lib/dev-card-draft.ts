import type { UserStack } from "./user";

const key = "devfeed:dev-card-draft";
export type DevCardDraft = { name: string; stack: UserStack[]; ready: boolean; created: number };
export function readDevCardDraft(): DevCardDraft | null {
  try {
    const draft = JSON.parse(sessionStorage.getItem(key) ?? "null");
    if (
      !draft ||
      typeof draft.name !== "string" ||
      !Array.isArray(draft.stack) ||
      typeof draft.created !== "number" ||
      typeof draft.ready !== "boolean" ||
      !draft.stack.every(
        (item: unknown) =>
          item &&
          typeof item === "object" &&
          "topic_id" in item &&
          typeof item.topic_id === "string" &&
          "name" in item &&
          typeof item.name === "string" &&
          "section" in item &&
          item.section === "primary",
      ) ||
      Date.now() - draft.created > 86400000
    )
      return null;
    return { ...draft, name: draft.name.slice(0, 100), stack: draft.stack.slice(0, 4) };
  } catch {
    return null;
  }
}
export function saveDevCardDraft(draft: DevCardDraft) {
  try {
    sessionStorage.setItem(key, JSON.stringify(draft));
    return true;
  } catch {
    return false;
  }
}
export function clearDevCardDraft() {
  try {
    sessionStorage.removeItem(key);
  } catch {
    /* Storage may be disabled. */
  }
}
