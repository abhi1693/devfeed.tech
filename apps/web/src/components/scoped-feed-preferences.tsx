"use client";

import { createContext, useContext, useEffect, useRef, useState } from "react";
import {
  effectiveFeedView,
  parseFeedViewScope,
  readGuestFeedView,
  writeGuestFeedView,
  type FeedViewScope,
} from "@/lib/feed-view-scope";
import type { FeedViewPreference } from "@/lib/reader-layouts";
import { userRequest } from "@/lib/user";
import { saveFeedView, validFeedViewReceipt } from "@/lib/user-mutations";
import { FeedPreferencesOverride, useFeedPreferences } from "./feed-preferences";
import { useUser } from "./user-account";

const Scope = createContext<FeedViewScope | null>(null);
export const useFeedViewScope = () => useContext(Scope);

type State = {
  owner: string;
  scope: FeedViewScope;
  key: string;
  view: FeedViewPreference;
  unavailable: boolean;
  error: string;
};
type Request = { key: string; controller: AbortController; ready: boolean; saving: boolean };

/** The route owns this stable scope, including while an intercepted article is open. */
export function ScopedFeedPreferences({
  scope: requestedScope,
  children,
}: {
  scope: FeedViewScope;
  children: React.ReactNode;
}) {
  const scope = parseFeedViewScope(requestedScope);
  if (!scope) throw new TypeError("Unsupported feed scope");
  return <ScopedPreferences scope={scope}>{children}</ScopedPreferences>;
}

function ScopedPreferences({
  scope,
  children,
}: {
  scope: FeedViewScope;
  children: React.ReactNode;
}) {
  const shared = useFeedPreferences();
  const { user, loading: checking, sessionRevision } = useUser();
  const owner = user?.user_id ?? "guest";
  const [revision, setRevision] = useState(0);
  const key = `${owner}:${scope}:${sessionRevision}:${revision}`;
  const [state, setState] = useState<State | null>(null);
  const [saving, setSaving] = useState<string | null>(null);
  const active = useRef<Request | null>(null);
  const legacyView = shared.storedView;
  const current = state?.owner === owner && state.scope === scope ? state : null;

  useEffect(() => {
    if (checking || shared.loading) return;
    const controller = new AbortController();
    const request: Request = { key, controller, ready: false, saving: false };
    active.current = request;
    const valid = () => active.current === request && !controller.signal.aborted;
    void Promise.resolve()
      .then(async () => {
        controller.signal.throwIfAborted();
        if (owner === "guest") return readGuestFeedView(scope, legacyView);
        const receipt: unknown = await userRequest(
          `settings/feed-view?feed=${encodeURIComponent(scope)}`,
          {
            signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]),
          },
        );
        if (!validFeedViewReceipt(receipt)) throw new Error("Invalid feed layout acknowledgement");
        return { view: effectiveFeedView(receipt.view, scope), unavailable: false };
      })
      .then((loaded) => {
        if (!valid()) return;
        request.ready = true;
        setSaving(null);
        setState({
          owner,
          scope,
          key,
          ...loaded,
          view: owner === "guest" && loaded.view === "columns" ? "cards" : loaded.view,
          error: "",
        });
      })
      .catch(() => {
        if (!valid()) return;
        request.ready = true;
        setSaving(null);
        setState((previous) => ({
          owner,
          scope,
          key,
          view:
            previous?.owner === owner && previous.scope === scope
              ? previous.view
              : effectiveFeedView(legacyView, scope),
          unavailable: true,
          error: "Couldn’t load this feed’s layout. Please try again.",
        }));
      });
    return () => controller.abort();
    // The user object can change its display name without changing the owner or session.
  }, [checking, key, legacyView, owner, scope, shared.loading]);

  async function setView(view: FeedViewPreference) {
    const request = active.current;
    if (
      !validFeedViewReceipt({ view }) ||
      (view === "columns" && (!user || scope !== "my_feed")) ||
      checking ||
      shared.loading ||
      shared.busy ||
      shared.unavailable ||
      !current ||
      current.unavailable ||
      current.key !== key ||
      !request?.ready ||
      request.key !== key ||
      request.saving ||
      request.controller.signal.aborted ||
      view === current.view
    )
      return false;
    const previous = current;
    const optimistic = { ...current, view, error: "" };
    request.saving = true;
    setSaving(key);
    setState(optimistic);
    const valid = () => active.current === request && !request.controller.signal.aborted;
    try {
      if (!user) {
        const saved = writeGuestFeedView(scope, view, legacyView);
        if (!saved && valid())
          setState({
            ...optimistic,
            error: "Couldn’t save your reader preferences in this browser.",
          });
        return saved;
      }
      const result = await saveFeedView(scope, view, {
        csrfToken: user.csrf_token,
        signal: AbortSignal.any([request.controller.signal, AbortSignal.timeout(15000)]),
      });
      if (!valid()) return false;
      setState({ ...optimistic, view: effectiveFeedView(result.view, scope) });
      return true;
    } catch {
      if (valid())
        setState({ ...previous, error: "Couldn’t save this feed’s layout. Please try again." });
      return false;
    } finally {
      request.saving = false;
      if (active.current === request) setSaving(null);
    }
  }

  return (
    <Scope.Provider value={scope}>
      <FeedPreferencesOverride
        value={{
          view: current?.view ?? "cards",
          storedView: current?.view ?? "cards",
          loading: shared.loading || !current,
          busy: shared.busy || checking || saving === key || (!!current && current.key !== key),
          unavailable: shared.unavailable || (current?.unavailable ?? false),
          error: current?.error || shared.error,
          setView,
          refresh: () =>
            shared.unavailable ? shared.refresh() : setRevision((value) => value + 1),
        }}
      >
        {children}
      </FeedPreferencesOverride>
    </Scope.Provider>
  );
}
