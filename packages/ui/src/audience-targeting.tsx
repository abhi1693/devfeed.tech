"use client";

import { useEffect, useId, useState } from "react";
import { ChevronLeft, ChevronRight, X } from "lucide-react";
import { Combobox } from "./combobox";

export type ExperienceLevel = "aspiring" | "entry_level" | "mid_level" | "senior";
export type Audience = {
  experience_levels: ExperienceLevel[];
  language_topic_ids: string[];
  tool_topic_ids: string[];
};
export type AudienceTopic = {
  id: string;
  name: string;
  slug: string;
  kind: string;
  status: string;
};
export type AudienceTopicLoader = (
  dimension: "languages" | "tools",
  params: { q?: string; offset?: number; limit?: number; topic_id?: string },
  signal: AbortSignal,
) => Promise<{ items: AudienceTopic[]; total: number }>;

export const experienceLevels: { value: ExperienceLevel; label: string }[] = [
  { value: "aspiring", label: "Aspiring developer" },
  { value: "entry_level", label: "Entry level" },
  { value: "mid_level", label: "Mid level" },
  { value: "senior", label: "Senior" },
];
const toolKinds = new Set([
  "tool",
  "platform",
  "framework",
  "library",
  "database",
  "runtime",
  "operating_system",
  "software",
  "service",
  "product",
  "package",
  "program",
  "technology",
]);
export function audienceValue(value?: Partial<Audience> | null): Audience {
  return {
    experience_levels: [...(value?.experience_levels ?? [])],
    language_topic_ids: [...(value?.language_topic_ids ?? [])],
    tool_topic_ids: [...(value?.tool_topic_ids ?? [])],
  };
}
export function hasAudience(value?: Partial<Audience> | null) {
  return Boolean(
    value?.experience_levels?.length ||
    value?.language_topic_ids?.length ||
    value?.tool_topic_ids?.length,
  );
}

function AudienceTopics({
  dimension,
  ids,
  onChange,
  loadTopics,
  disabled,
}: {
  dimension: "languages" | "tools";
  ids: string[];
  onChange?: (ids: string[]) => void;
  loadTopics: AudienceTopicLoader;
  disabled?: boolean;
}) {
  const label = dimension === "languages" ? "Programming languages" : "Developer tools";
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [page, setPage] = useState({ q: "", offset: 0 });
  const [attempt, setAttempt] = useState(0);
  const [known, setKnown] = useState<Record<string, AudienceTopic>>({});
  const [result, setResult] = useState<{
    items: AudienceTopic[];
    total: number;
    key: string;
    error?: string;
  }>({ items: [], total: 0, key: "" });
  const requestKey = JSON.stringify([dimension, page.q, page.offset, attempt]);
  const loading = result.key !== requestKey || search.trim() !== page.q;
  const [resolutionError, setResolutionError] = useState(false);
  const selected = ids.join(",");
  useEffect(() => {
    if (!open || search.trim() === page.q) return;
    const timer = setTimeout(() => setPage({ q: search.trim(), offset: 0 }), 250);
    return () => clearTimeout(timer);
  }, [open, search, page.q]);
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    void loadTopics(dimension, { ...page, limit: 25 }, controller.signal)
      .then((next) => {
        if (controller.signal.aborted) return;
        setResult({ ...next, key: requestKey });
        setKnown((previous) => ({
          ...previous,
          ...Object.fromEntries(next.items.map((topic) => [topic.id, topic])),
        }));
      })
      .catch(() => {
        if (!controller.signal.aborted)
          setResult({ items: [], total: 0, key: requestKey, error: "Could not load options." });
      });
    return () => controller.abort();
  }, [open, page, dimension, loadTopics, requestKey]);
  useEffect(() => {
    const missing = selected.split(",").filter((id) => id && !known[id]);
    if (!missing.length) return;
    const controller = new AbortController();
    let index = 0;
    let failed = false;
    const resolved: Record<string, AudienceTopic> = {};
    async function worker() {
      while (index < missing.length && !controller.signal.aborted) {
        const id = missing[index++];
        try {
          const response = await loadTopics(
            dimension,
            { topic_id: id, limit: 1 },
            controller.signal,
          );
          const topic = response.items.find((item) => item.id === id);
          if (topic) resolved[id] = topic;
          else failed = true;
        } catch {
          failed = true;
        }
      }
    }
    void Promise.all(Array.from({ length: Math.min(4, missing.length) }, worker)).then(() => {
      if (controller.signal.aborted) return;
      setKnown((previous) => ({ ...previous, ...resolved }));
      setResolutionError(failed);
    });
    return () => controller.abort();
    // The selection/retry identifies this request; catalog results only seed the name cache.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected, attempt, dimension, loadTopics]);
  return (
    <div className="min-w-0 space-y-3" role="group" aria-label={label}>
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-sm font-medium">{label}</h3>
        {onChange && ids.length > 0 && (
          <button
            type="button"
            className="text-xs text-muted-foreground underline underline-offset-4"
            disabled={disabled}
            aria-label={`Any ${label.toLowerCase()}`}
            onClick={() => onChange([])}
          >
            Any
          </button>
        )}
      </div>
      {ids.length ? (
        <ul className="flex flex-wrap gap-2" aria-label={`Selected ${label.toLowerCase()}`}>
          {ids.map((id) => (
            <li
              key={id}
              className="flex max-w-full items-center gap-1 rounded-md border px-2 py-1 text-sm"
            >
              <span className="min-w-0 break-words">
                {known[id]?.name ??
                  (resolutionError ? "Unavailable selection" : "Loading selection…")}
              </span>
              {known[id]?.status !== undefined && known[id].status !== "active" && (
                <span className="text-xs text-muted-foreground">(retired)</span>
              )}
              {known[id] &&
                (dimension === "languages"
                  ? known[id].kind !== "language"
                  : !toolKinds.has(known[id].kind)) && (
                  <span className="text-xs text-muted-foreground">(unavailable kind)</span>
                )}
              {onChange && (
                <button
                  type="button"
                  className="shrink-0 rounded p-1 hover:bg-muted focus-visible:outline-2"
                  disabled={disabled}
                  aria-label={`Remove ${known[id]?.name ?? "unavailable selection"}`}
                  onClick={() => onChange(ids.filter((value) => value !== id))}
                >
                  <X aria-hidden size={12} />
                </button>
              )}
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-muted-foreground">Any</p>
      )}
      {resolutionError && ids.some((id) => !known[id]) && (
        <p role="alert" className="text-sm text-destructive">
          Could not resolve a saved selection.{" "}
          <button type="button" className="underline" onClick={() => setAttempt((n) => n + 1)}>
            Retry names
          </button>
        </p>
      )}
      {onChange && (
        <Combobox
          label={label}
          value=""
          disabled={disabled || ids.length >= 100}
          placeholder={`Add ${dimension === "languages" ? "a language" : "a developer tool"}…`}
          clearLabel={`Any ${label.toLowerCase()}`}
          options={result.items
            .filter((topic) => !ids.includes(topic.id))
            .map((topic) => ({
              value: topic.id,
              label: topic.name,
              description: topic.kind.replaceAll("_", " "),
            }))}
          onChange={(id) => onChange(id ? [...new Set([...ids, id])] : [])}
          search={search}
          searchPlaceholder={`Search ${label.toLowerCase()}…`}
          onSearchChange={setSearch}
          onOpenChange={(next) => {
            setOpen(next);
            setSearch("");
            setPage({ q: "", offset: 0 });
            if (next) setAttempt((value) => value + 1);
          }}
          loading={loading}
          error={result.error}
          onRetry={() => setAttempt((n) => n + 1)}
          footer={
            <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
              <span>{result.total.toLocaleString()} matching topics</span>
              <div className="flex items-center gap-1">
                <button
                  type="button"
                  className="rounded p-2 hover:bg-muted disabled:opacity-40"
                  aria-label={`Previous ${label.toLowerCase()}`}
                  disabled={loading || page.offset === 0}
                  onClick={() =>
                    setPage((current) => ({ ...current, offset: Math.max(0, current.offset - 25) }))
                  }
                >
                  <ChevronLeft aria-hidden size={16} />
                </button>
                <button
                  type="button"
                  className="rounded p-2 hover:bg-muted disabled:opacity-40"
                  aria-label={`Next ${label.toLowerCase()}`}
                  disabled={loading || page.offset + 25 >= result.total}
                  onClick={() =>
                    setPage((current) => ({ ...current, offset: current.offset + 25 }))
                  }
                >
                  <ChevronRight aria-hidden size={16} />
                </button>
              </div>
            </div>
          }
        />
      )}
    </div>
  );
}

export function AudienceTargeting({
  value,
  onChange,
  loadTopics,
  disabled = false,
}: {
  value: Audience;
  onChange?: (value: Audience) => void;
  loadTopics: AudienceTopicLoader;
  disabled?: boolean;
}) {
  const id = useId();
  return (
    <div className="grid min-w-0 gap-5 sm:grid-cols-3">
      <fieldset className="min-w-0 space-y-3" disabled={disabled}>
        <legend className="mb-3 text-sm font-medium">Experience levels</legend>
        {onChange ? (
          experienceLevels.map((level) => (
            <label
              key={level.value}
              className="flex items-center gap-2 text-sm"
              htmlFor={`${id}-${level.value}`}
            >
              <input
                id={`${id}-${level.value}`}
                type="checkbox"
                checked={value.experience_levels.includes(level.value)}
                onChange={(event) =>
                  onChange({
                    ...value,
                    experience_levels: event.target.checked
                      ? [...value.experience_levels, level.value]
                      : value.experience_levels.filter((entry) => entry !== level.value),
                  })
                }
              />
              {level.label}
            </label>
          ))
        ) : (
          <p className="text-sm text-muted-foreground">
            {experienceLevels
              .filter((level) => value.experience_levels.includes(level.value))
              .map((level) => level.label)
              .join(", ") || "Any"}
          </p>
        )}
        {onChange && <p className="text-xs text-muted-foreground">No selection means Any.</p>}
      </fieldset>
      <AudienceTopics
        dimension="languages"
        ids={value.language_topic_ids}
        loadTopics={loadTopics}
        disabled={disabled}
        onChange={
          onChange ? (language_topic_ids) => onChange({ ...value, language_topic_ids }) : undefined
        }
      />
      <AudienceTopics
        dimension="tools"
        ids={value.tool_topic_ids}
        loadTopics={loadTopics}
        disabled={disabled}
        onChange={onChange ? (tool_topic_ids) => onChange({ ...value, tool_topic_ids }) : undefined}
      />
    </div>
  );
}
