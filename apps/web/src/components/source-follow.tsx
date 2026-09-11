"use client";
import { createContext, useContext, useEffect, useRef, useState } from "react";
import { Check, Plus, LoaderCircle } from "lucide-react";
import { AccountError, userRequest } from "@/lib/user";
import { useUser } from "./user-account";

type Preferences = { source_ids: string[] };
const Context = createContext({ ids: [] as string[], loading: true, unavailable: false, busy: [] as string[], error: "", refresh: () => {}, toggle: async (_id: string) => { void _id; }, save: async (_ids: string[]) => { void _ids; return false; } });
export const useSourceFollows = () => useContext(Context);
export function SourceFollowsProvider({ children }: { children: React.ReactNode }) {
  const { user, loading } = useUser();
  const owner = user?.user_id ?? "guest";
  const [state, setState] = useState<{ owner: string; ids: string[]; unavailable: boolean } | null>(null);
  const [busy, setBusy] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const request = useRef<AbortController | null>(null);
  const mutations = useRef(new Set<string>());
  useEffect(() => {
    if (!user) return;
    const controller = new AbortController(); request.current = controller;
    userRequest<Preferences>("preferences/sources", { signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]) }).then(value => {
      if (!controller.signal.aborted) setState({ owner, ids: value.source_ids, unavailable: false });
    }).catch(() => { if (!controller.signal.aborted) setState({ owner, ids: [], unavailable: true }); });
    return () => controller.abort();
  }, [user, owner, revision]);
  const current = state?.owner === owner ? state : null;
  async function mutate(ids: string[], id?: string) {
    if (!user || !current || current.unavailable || mutations.current.has(id ?? "all") || mutations.current.has("all") || (!id && mutations.current.size)) return false;
    const token = id ?? "all"; const controller = request.current;
    if (!controller || controller.signal.aborted) return false;
    mutations.current.add(token); setBusy([...mutations.current]); setError("");
    try {
      const followed = id ? !current.ids.includes(id) : false;
      const result = await userRequest<Preferences | { followed: boolean }>(`preferences/sources${id ? `/${id}` : ""}`, {
        method: "PUT", headers: { "Content-Type": "application/json", "X-CSRF-Token": user.csrf_token },
        body: JSON.stringify(id ? { followed } : { source_ids: ids }), signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]),
      });
      if (controller.signal.aborted) return false;
      setState(previous => ({ owner, unavailable: false, ids: "source_ids" in result ? result.source_ids : result.followed ? [...new Set([...(previous?.owner === owner ? previous.ids : []), id!])] : (previous?.ids ?? []).filter(value => value !== id) }));
      return true;
    } catch (cause) {
      if (!controller.signal.aborted) setError(cause instanceof AccountError && cause.status === 422 ? "Choose up to 100 approved sources." : "Couldn’t update your sources. Please try again.");
      return false;
    } finally { mutations.current.delete(token); setBusy([...mutations.current]); }
  }
  return <Context.Provider value={{ ids: current?.ids ?? [], loading: loading || (!!user && !current), unavailable: !!current?.unavailable, busy, error, refresh: () => setRevision(value => value + 1), toggle: async id => { await mutate([], id); }, save: ids => mutate(ids) }}>{children}</Context.Provider>;
}
export function SourceFollow({ sourceId, returnTo }: { sourceId: string; returnTo: string }) {
  const { user } = useUser();
  const { ids, loading, unavailable, busy, error, toggle, refresh } = useSourceFollows();
  const followed = ids.includes(sourceId), pending = busy.includes(sourceId) || busy.includes("all");
  return <div className="topic-follow source-follow">
    {!loading && !user ? <a className="button follow-button" href={`/api/v1/user/auth/login?return_to=${encodeURIComponent(returnTo)}`}><Plus size={16} aria-hidden />Follow source</a> :
      <button className="button follow-button" type="button" aria-pressed={followed} disabled={loading || pending || unavailable} onClick={() => void toggle(sourceId)}>
        {pending ? <LoaderCircle size={16} className="settings-spinner" aria-hidden /> : followed ? <Check size={16} aria-hidden /> : <Plus size={16} aria-hidden />}{pending ? "Saving…" : followed ? "Following source" : "Follow source"}
      </button>}
    {unavailable && <button className="settings-button" onClick={refresh}>Retry source preferences</button>}
    {error && <p role="alert">{error}</p>}
  </div>;
}
