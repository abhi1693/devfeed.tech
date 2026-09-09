import { resources, type Resource } from "./resources";
import type { RecordData } from "./resource-api";
import type { TopicFact } from "./api/generated/models";
export type EditableFact = TopicFact & { originalRetrievedAt?: string };
export function localDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}
export function initialFacts(facts: TopicFact[] = []): EditableFact[] {
  return facts.map(fact => ({ ...fact, retrieved_at: localDate(fact.retrieved_at), originalRetrievedAt: fact.retrieved_at }));
}
export function factsPayload(facts: EditableFact[]): TopicFact[] {
  return facts.map(({ originalRetrievedAt, ...fact }) => ({
    ...fact,
    retrieved_at: originalRetrievedAt && fact.retrieved_at === localDate(originalRetrievedAt)
      ? originalRetrievedAt : new Date(fact.retrieved_at).toISOString(),
  }));
}
export function lineValues(value: unknown): string[] {
  return [...new Set(String(value || "").split(/\r?\n/).map(item => item.trim()).filter(Boolean))];
}
export function initialValues(resource: Resource, record?: RecordData): Record<string, unknown> {
  return Object.fromEntries(resources[resource].fields.map(field => {
    let value = record?.[field.key] ?? field.default ?? (field.type === "boolean" ? false : "");
    if (field.type === "lines") value = Array.isArray(value) ? value.join("\n") : value;
    if (field.type === "datetime" && value) value = localDate(String(value));
    return [field.key, value];
  }));
}
export function formPayload(resource: Resource, values: Record<string, unknown>, record?: RecordData) {
  const result: Record<string, unknown> = {};
  for (const field of resources[resource].fields) {
    if (record && field.createOnly && resource !== "topic-relations") continue;
    let value = values[field.key];
    if (field.type === "lines") value = lineValues(value);
    else if (field.type === "number") value = Number(value);
    else if (field.type === "datetime") value = value ? record?.[field.key] && value === localDate(String(record[field.key])) ? record[field.key] : new Date(String(value)).toISOString() : null;
    else if (field.type !== "boolean" && value === "") value = field.key === "summary" ? "" : null;
    result[field.key] = value;
  }
  if (record && resource === "articles") result.expected_revision = record.editorial_revision;
  return result;
}
