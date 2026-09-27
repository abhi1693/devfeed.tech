"use client";

import { createContext, useContext, useEffect, useRef, useState } from "react";
import { AccountError, userRequest, type Preferences } from "@/lib/user";
import { useUser } from "./user-account";

const Context = createContext({
  ids: [] as string[],
  loading: true,
  unavailable: false,
  busy: [] as string[],
  errors: {} as Record<string, string>,
  messages: {} as Record<string, string>,
  refresh: () => {},
  toggle: async (_id: string) => {
    void _id;
  },
  save: async (_ids: string[]): Promise<Preferences | null> => {
    void _ids;
    return null;
  },
});
export const useTopicFollows = () => useContext(Context);

export function TopicFollowsProvider({ children }: { children: React.ReactNode }) {
  const { user, loading, sessionRevision } = useUser();
  const owner = user?.user_id ?? "guest";
  const [state, setState] = useState<{
    owner: string;
    ids: string[];
    unavailable: boolean;
    signal: AbortSignal;
  } | null>(null);
  const [feedback, setFeedback] = useState({
    owner,
    signal: null as AbortSignal | null,
    busy: [] as string[],
    errors: {} as Record<string, string>,
    messages: {} as Record<string, string>,
  });
  const [revision, setRevision] = useState(0);
  const request = useRef<{ owner: string; controller: AbortController; failed: boolean } | null>(
    null,
  );
  const mutations = useRef(new Map<string, AbortController>());
  useEffect(() => {
    if (loading || owner === "guest") return;
    const controller = new AbortController();
    const active = { owner, controller, failed: false };
    request.current = active;
    userRequest<Preferences>("preferences", {
      signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]),
    })
      .then((value) => {
        if (!controller.signal.aborted)
          setState({ owner, ids: value.topic_ids, unavailable: false, signal: controller.signal });
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          active.failed = true;
          setState({ owner, ids: [], unavailable: true, signal: controller.signal });
        }
      });
    return () => controller.abort();
  }, [owner, loading, revision, sessionRevision]);
  useEffect(() => {
    const resume = () => {
      if (document.visibilityState === "visible" && request.current?.failed) {
        request.current.failed = false;
        setRevision((value) => value + 1);
      }
    };
    window.addEventListener("focus", resume);
    document.addEventListener("visibilitychange", resume);
    return () => {
      window.removeEventListener("focus", resume);
      document.removeEventListener("visibilitychange", resume);
    };
  }, []);
  const current = state?.owner === owner && !state.signal.aborted ? state : null;
  const currentFeedback = feedback.owner === owner && !feedback.signal?.aborted ? feedback : null;
  async function mutate(ids: string[], id?: string): Promise<Preferences | null> {
    const active = request.current;
    const token = id ?? "all";
    const pending = [...mutations.current.entries()].filter(
      ([, controller]) => !controller.signal.aborted,
    );
    if (
      !user ||
      !current ||
      current.unavailable ||
      !active ||
      active.owner !== owner ||
      active.controller.signal.aborted ||
      pending.some(([key]) => key === token || key === "all") ||
      (!id && pending.length)
    )
      return null;
    const controller = active.controller;
    mutations.current.set(token, controller);
    setFeedback((previous) => ({
      owner,
      signal: controller.signal,
      busy: [...pending.map(([key]) => key), token],
      errors: { ...(previous.owner === owner ? previous.errors : {}), [token]: "" },
      messages: { ...(previous.owner === owner ? previous.messages : {}), [token]: "" },
    }));
    try {
      const result = await userRequest<Preferences | { followed: boolean }>(
        id ? `preferences/topics/${id}` : "preferences",
        {
          method: "PUT",
          headers: { "Content-Type": "application/json", "X-CSRF-Token": user.csrf_token },
          body: JSON.stringify(id ? { followed: !current.ids.includes(id) } : { topic_ids: ids }),
          signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]),
        },
      );
      if (controller.signal.aborted) return null;
      setState((previous) => ({
        owner,
        signal: controller.signal,
        unavailable: false,
        ids:
          "topic_ids" in result
            ? result.topic_ids
            : result.followed
              ? [...new Set([...(previous?.owner === owner ? previous.ids : []), id!])]
              : (previous?.owner === owner ? previous.ids : []).filter((value) => value !== id),
      }));
      if ("followed" in result)
        setFeedback((previous) => ({
          ...previous,
          messages: {
            ...previous.messages,
            [token]: result.followed ? "Topic followed." : "Topic unfollowed.",
          },
        }));
      return "topic_ids" in result
        ? result
        : {
            topic_ids: result.followed
              ? [...new Set([...current.ids, id!])]
              : current.ids.filter((value) => value !== id),
          };
    } catch (error) {
      if (!controller.signal.aborted)
        setFeedback((previous) => ({
          ...previous,
          errors: {
            ...previous.errors,
            [token]:
              error instanceof AccountError && error.status === 422
                ? "You can follow up to 100 active topics. Manage your topics to make room."
                : "Couldn’t update this topic. Please try again.",
          },
        }));
      return null;
    } finally {
      if (mutations.current.get(token) === controller) mutations.current.delete(token);
      if (!controller.signal.aborted)
        setFeedback((previous) => ({
          ...previous,
          busy: [...mutations.current.entries()]
            .filter(([, value]) => !value.signal.aborted)
            .map(([key]) => key),
        }));
    }
  }
  return (
    <Context.Provider
      value={{
        ids: current?.ids ?? [],
        loading: loading || (!!user && !current),
        unavailable: !!current?.unavailable,
        busy: currentFeedback?.busy ?? [],
        errors: currentFeedback?.errors ?? {},
        messages: currentFeedback?.messages ?? {},
        refresh: () => setRevision((value) => value + 1),
        toggle: async (id) => {
          await mutate([], id);
        },
        save: (ids) => mutate(ids),
      }}
    >
      {children}
    </Context.Provider>
  );
}
