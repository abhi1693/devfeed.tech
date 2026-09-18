"use client";
import { LoadingReveal } from "./loading-reveal";
import { SaveFeedback } from "./motion-icon";
import { InfiniteChoices } from "./infinite-choices";
import { SuggestSourceLink } from "./source-suggestion";
import { Search } from "lucide-react";
import { useState } from "react";
import { RetryButton } from "@devfeed/ui/retry-button";
import { CatalogIcon } from "./catalog-icon";
import { LoadingSkeleton } from "./loading-skeleton";
import { UserSettingsLayout } from "./user-settings-layout";
import { AccountGate } from "./user-account";
import { useSourceFollows } from "./source-follow";
import type { Source } from "@/lib/types";
export function SourcePreferences({ sources }: { sources?: Source[] }) {
  return (
    <UserSettingsLayout section="sources">
      <section className="profile-panel">
        <AccountGate
          returnTo="/settings/sources"
          loadingFallback={<LoadingSkeleton kind="sources" label="Loading your sources…" />}
        >
          <SourceChoices sources={sources} />
        </AccountGate>
      </section>
    </UserSettingsLayout>
  );
}
function SourceChoices({ sources }: { sources?: Source[] }) {
  const state = useSourceFollows();
  if (state.loading)
    return (
      <LoadingReveal
        loading
        fallback={<LoadingSkeleton kind="sources" label="Loading your sources…" />}
      />
    );
  if (state.unavailable)
    return (
      <div role="status">
        <p>Couldn’t load your sources.</p>
        <RetryButton onRetry={state.refresh} />
      </div>
    );
  return (
    <LoadingReveal
      loading={false}
      fallback={<LoadingSkeleton kind="sources" label="Loading your sources…" />}
    >
      <SourceSelection sources={sources} />
    </LoadingReveal>
  );
}
function SourceSelection({ sources }: { sources?: Source[] }) {
  const { ids, save, busy, error } = useSourceFollows();
  const [selected, setSelected] = useState(ids);
  const [query, setQuery] = useState("");
  const [message, setMessage] = useState("");
  const changed = selected.length !== ids.length || selected.some((id) => !ids.includes(id));
  return (
    <div className="source-preferences">
      <p className="profile-description">Choose up to 100 sources for your feed.</p>
      <div className="source-selection-toolbar">
        <label className="source-selection-search">
          <span className="sr-only">Find a source</span>
          <Search size={16} aria-hidden="true" />
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search sources"
          />
        </label>
        <p className="source-selection-status" role="status">
          {message || `${selected.length} sources selected`}
        </p>
        <SuggestSourceLink />
      </div>
      <InfiniteChoices items={sources} label="sources" query={query}>
        {(choices) => (
          <div className="source-choice-grid">
            {choices.map((source) => {
              const active = selected.includes(source.id);
              return (
                <button
                  key={source.id}
                  type="button"
                  className="source-choice"
                  title={source.name}
                  aria-label={source.name}
                  aria-pressed={active}
                  disabled={!!busy.length || (!active && selected.length >= 100)}
                  onClick={() => {
                    setSelected(
                      active ? selected.filter((id) => id !== source.id) : [...selected, source.id],
                    );
                    setMessage("");
                  }}
                >
                  <CatalogIcon url={source.logo_url} source />
                  <span className="source-choice-name">{source.name}</span>
                </button>
              );
            })}
          </div>
        )}
      </InfiniteChoices>
      {error && <p role="alert">{error}</p>}
      <div className="profile-form-actions">
        <button
          className="settings-button settings-button-ghost"
          disabled={!!busy.length || !selected.length}
          onClick={() => {
            setSelected([]);
            setMessage("");
          }}
        >
          Clear selection
        </button>
        <button
          className="settings-button"
          disabled={!!busy.length || !changed}
          onClick={async () => {
            if (await save(selected)) setMessage("Your sources are saved.");
          }}
        >
          <SaveFeedback busy={!!busy.length} saved={!!message && !error && !changed} />
          {busy.length ? "Please wait…" : "Save sources"}
        </button>
      </div>
    </div>
  );
}
