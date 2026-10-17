"use client";

import { useMutation, useMutationState, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { useUser } from "@/components/user-account";
import { AccountError, userRequest } from "./user";
import {
  saveSourcePreferences,
  saveTagPreferences,
  setTagFollow,
  saveTopicPreferences,
  setSourceFollow,
  setTopicFollow,
  type MutationOptions,
} from "./user-mutations";

const collections = {
  tag: {
    path: "preferences/tags",
    field: "tag_ids",
    setFollow: setTagFollow,
    save: async (ids: string[], options: MutationOptions) => ({
      ids: (await saveTagPreferences(ids, options)).tag_ids,
    }),
    limitError: "You can follow up to 100 published tags.",
    error: "Couldn’t update this tag. Please try again.",
    followed: "Tag followed.",
    unfollowed: "Tag unfollowed.",
  },
  topic: {
    path: "preferences",
    field: "topic_ids",
    setFollow: setTopicFollow,
    save: async (ids: string[], options: MutationOptions) => ({
      ids: (await saveTopicPreferences(ids, options)).topic_ids,
    }),
    limitError: "You can follow up to 100 active topics. Manage your topics to make room.",
    error: "Couldn’t update this topic. Please try again.",
    followed: "Topic followed.",
    unfollowed: "Topic unfollowed.",
  },
  source: {
    path: "preferences/sources",
    field: "source_ids",
    setFollow: setSourceFollow,
    save: async (ids: string[], options: MutationOptions) => ({
      ids: (await saveSourcePreferences(ids, options)).source_ids,
    }),
    limitError: "Choose up to 100 approved sources.",
    error: "Couldn’t update your sources. Please try again.",
    followed: "Source followed.",
    unfollowed: "Source unfollowed.",
  },
} as const;
type Collection = keyof typeof collections;
type Key = readonly ["reader", string | undefined, number, "follows", Collection];
type Write = {
  key: Key;
  id?: string;
  ids: string[];
  followed: boolean;
  csrf: string;
  signal: AbortSignal;
};
type Result = { ids: string[] } | { followed: boolean };
type Payload = { topic_ids?: string[]; source_ids?: string[]; tag_ids?: string[] };

/** The same account-safe query and write lifecycle serves source and topic follows. */
export function useFollowPreferences(kind: Collection) {
  const { user, loading, sessionRevision } = useUser();
  const client = useQueryClient();
  const config = collections[kind];
  const userId = user?.user_id;
  const key: Key = useMemo(
    () => ["reader", userId, sessionRevision, "follows", kind],
    [userId, sessionRevision, kind],
  );
  const mutationKey = [...key, "write"];
  const scope = useRef<{ key: Key; controller: AbortController } | null>(null);
  const [feedback, setFeedback] = useState<{
    key: Key;
    errors: Record<string, string>;
    messages: Record<string, string>;
  }>({ key, errors: {}, messages: {} });
  useEffect(() => {
    const controller = new AbortController();
    scope.current = { key, controller };
    return () => controller.abort();
  }, [key]);
  const query = useQuery({
    queryKey: key,
    enabled: !loading && !!userId,
    queryFn: async ({ signal }) => {
      const result = await userRequest<Payload>(config.path, {
        signal: AbortSignal.any([signal, AbortSignal.timeout(15000)]),
      });
      const ids = result[config.field];
      if (!Array.isArray(ids)) throw new Error("Invalid follow preferences");
      return ids;
    },
  });
  const busy = useMutationState({
    filters: { mutationKey, exact: true, status: "pending" },
    select: (mutation) => (mutation.state.variables as Write).id ?? "all",
  });
  const pending = () =>
    client
      .getMutationCache()
      .findAll({ mutationKey, exact: true, status: "pending" })
      .map((mutation) => (mutation.state.variables as Write).id ?? "all");
  const mutation = useMutation({
    mutationKey,
    onMutate: async (write: Write) => {
      // A read started before this write must not replace the acknowledged result.
      await client.cancelQueries({ queryKey: write.key, exact: true });
    },
    mutationFn: async (write: Write): Promise<Result> => {
      const options = {
        csrfToken: write.csrf,
        signal: AbortSignal.any([write.signal, AbortSignal.timeout(15000)]),
      };
      return write.id
        ? config.setFollow(write.id, write.followed, options)
        : config.save(write.ids, options);
    },
    onSuccess: (result, write) => {
      if (write.signal.aborted) return;
      client.setQueryData<string[]>(write.key, (previous = []) =>
        "ids" in result
          ? result.ids
          : result.followed
            ? [...new Set([...previous, write.id!])]
            : previous.filter((id) => id !== write.id),
      );
      if ("followed" in result)
        setFeedback((previous) => ({
          key: write.key,
          errors: previous.key === write.key ? previous.errors : {},
          messages: {
            ...(previous.key === write.key ? previous.messages : {}),
            [write.id!]: result.followed ? config.followed : config.unfollowed,
          },
        }));
    },
    onError: (error, write) => {
      if (write.signal.aborted) return;
      setFeedback((previous) => ({
        key: write.key,
        messages: previous.key === write.key ? previous.messages : {},
        errors: {
          ...(previous.key === write.key ? previous.errors : {}),
          [write.id ?? "all"]:
            error instanceof AccountError && error.status === 422
              ? config.limitError
              : config.error,
        },
      }));
    },
  });
  const { refetch, isError } = query;
  useEffect(() => {
    if (!userId || !isError) return;
    const resume = () => {
      if (document.visibilityState === "visible") void refetch({ cancelRefetch: false });
    };
    window.addEventListener("focus", resume);
    document.addEventListener("visibilitychange", resume);
    return () => {
      window.removeEventListener("focus", resume);
      document.removeEventListener("visibilitychange", resume);
    };
  }, [userId, isError, refetch]);
  async function mutate(ids: string[], id?: string): Promise<string[] | null> {
    const active = scope.current;
    const writes = pending();
    if (
      !user ||
      loading ||
      !query.data ||
      query.isError ||
      active?.key !== key ||
      active.controller.signal.aborted ||
      writes.includes(id ?? "all") ||
      writes.includes("all") ||
      (!id && writes.length)
    )
      return null;
    const token = id ?? "all";
    setFeedback((previous) => ({
      key,
      errors: { ...(previous.key === key && kind === "topic" ? previous.errors : {}), [token]: "" },
      messages: { ...(previous.key === key ? previous.messages : {}), [token]: "" },
    }));
    const signal = active.controller.signal;
    try {
      await mutation.mutateAsync({
        key,
        ids,
        id,
        followed: !(client.getQueryData<string[]>(key) ?? []).includes(id!),
        csrf: user.csrf_token,
        signal,
      });
      return signal.aborted ? null : (client.getQueryData<string[]>(key) ?? null);
    } catch {
      return null;
    }
  }
  return {
    ids: query.data ?? [],
    loading: loading || (!!user && query.isPending),
    unavailable: !!user && query.isError,
    busy,
    errors: feedback.key === key ? feedback.errors : {},
    messages: feedback.key === key ? feedback.messages : {},
    refresh: () => {
      if (!loading && userId && !pending().length) void refetch({ cancelRefetch: false });
    },
    toggle: async (id: string) => {
      await mutate([], id);
    },
    save: (ids: string[]) => mutate(ids),
  };
}
