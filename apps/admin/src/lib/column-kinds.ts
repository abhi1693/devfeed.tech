export type ColumnKind =
  | "text"
  | "name"
  | "slug"
  | "boolean"
  | "tags"
  | "pill"
  | "datetime"
  | "label"
  | "language"
  | "number";

export type PillTone = "success" | "warning" | "info" | "danger" | "neutral";

export type ColumnPresentation = {
  kind?: ColumnKind;
  tone?: PillTone;
};
