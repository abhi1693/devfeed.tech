"use client";
import { useRef, useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { Select } from "@devfeed/ui/select";
import type { UserStack } from "@/lib/user";
import type { Topic } from "@/lib/types";
import { CatalogIcon } from "./catalog-icon";
import { InfiniteChoices } from "./infinite-choices";
export function ProfileStackEditor({
  stack,
  onChange,
}: {
  stack: UserStack[];
  onChange: (stack: UserStack[]) => void;
}) {
  const [query, setQuery] = useState("");
  const searchRef = useRef<HTMLInputElement>(null);
  const resultsRef = useRef<HTMLDivElement>(null);
  const sections = {
    primary: "Use regularly",
    hobby: "Side projects",
    learning: "Learning",
    past: "Used before",
  } as const;
  return (
    <section className="profile-direct-section" aria-label="Developer stack">
      <div className="profile-direct-section-heading">
        <h3>Developer stack</h3>
        <span>Usage and year are optional</span>
      </div>
      <input
        ref={searchRef}
        type="search"
        aria-label="Find a language, framework, or tool"
        placeholder="Type to add a language, framework, or tool…"
        value={query}
        maxLength={200}
        disabled={stack.length >= 100}
        onChange={(event) => setQuery(event.target.value)}
        onKeyDown={(event) => {
          if (event.nativeEvent.isComposing) return;
          if (event.key === "Escape") {
            event.preventDefault();
            setQuery("");
          }
          if (event.key === "ArrowDown") {
            event.preventDefault();
            resultsRef.current?.querySelector<HTMLButtonElement>("button[data-add-topic]")?.focus();
          }
          if (event.key === "Enter") {
            event.preventDefault();
            resultsRef.current?.querySelector<HTMLButtonElement>("button[data-add-topic]")?.click();
          }
        }}
      />
      {query.trim() && (
        <div ref={resultsRef} className="profile-direct-results" aria-label="Matching stack topics">
          <InfiniteChoices<Topic> label="topics" query={query}>
            {(topics, complete) => {
              const available = topics.filter(
                (topic) => !stack.some((entry) => entry.topic_id === topic.id),
              );
              return (
                <>
                  {available.map((topic) => (
                    <button
                      data-add-topic
                      key={topic.id}
                      type="button"
                      aria-label={`Add ${topic.name}`}
                      onClick={() => {
                        onChange([
                          ...stack,
                          {
                            topic_id: topic.id,
                            name: topic.name,
                            slug: topic.slug,
                            kind: topic.kind,
                            logo_url: topic.logo_url,
                            status: "active",
                            section: "primary",
                            since_year: null,
                          },
                        ]);
                        setQuery("");
                        searchRef.current?.focus();
                      }}
                    >
                      <CatalogIcon url={topic.logo_url} kind={topic.kind} iconSize={16} />
                      <span className="profile-stack-result-name">{topic.name}</span>
                      <span className="profile-stack-result-kind">
                        {topic.kind.replaceAll("_", " ")}
                      </span>
                      <Plus className="profile-stack-result-add" size={16} aria-hidden="true" />
                    </button>
                  ))}
                  {complete && topics.length > 0 && !available.length && (
                    <p>Matching technologies are already in your profile.</p>
                  )}
                </>
              );
            }}
          </InfiniteChoices>
        </div>
      )}
      {stack.length > 0 && (
        <div className="profile-direct-technologies">
          {stack.map((item) => (
            <div className="profile-direct-technology" key={item.topic_id}>
              <span className="profile-direct-technology-name">
                <CatalogIcon url={item.logo_url} kind={item.kind} iconSize={16} />
                <span>
                  {item.name}
                  {item.status && item.status !== "active" && <small>No longer listed</small>}
                </span>
              </span>
              <Select
                label={`Usage for ${item.name}`}
                required
                value={item.section || "primary"}
                options={Object.entries(sections).map(([value, label]) => ({ value, label }))}
                onChange={(section) =>
                  onChange(
                    stack.map((entry) =>
                      entry.topic_id === item.topic_id
                        ? { ...entry, section: section as UserStack["section"] }
                        : entry,
                    ),
                  )
                }
              />
              <input
                aria-label={`Since year for ${item.name}`}
                type="number"
                min={1900}
                max={new Date().getUTCFullYear()}
                placeholder="Since year"
                value={item.since_year ?? ""}
                onChange={(event) =>
                  onChange(
                    stack.map((entry) =>
                      entry.topic_id === item.topic_id
                        ? {
                            ...entry,
                            since_year: event.target.value ? Number(event.target.value) : null,
                          }
                        : entry,
                    ),
                  )
                }
              />
              <button
                type="button"
                className="settings-icon-button"
                aria-label={`Remove ${item.name}`}
                onClick={() => onChange(stack.filter((entry) => entry.topic_id !== item.topic_id))}
              >
                <Trash2 size={15} aria-hidden="true" />
              </button>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
