"use client";

import { createContext, useContext, useEffect, useState } from "react";
import { preferencesChanged } from "@devfeed/ui/notifications";
import { runWhenPageActive } from "@devfeed/ui/page-activity";
import { AccountError, userRequest } from "@/lib/user";
import { useUser } from "./user-account";

export type NotificationDisplay = { show_badge: boolean; sound: boolean };
export const notificationDefaults: NotificationDisplay = { show_badge: true, sound: false };
export const topicNotificationCategory = "feed.topic.new";
export const userNotificationLabels = {
  [topicNotificationCategory]: "New articles from your topics and sources",
};
type State = { owner: string; value: NotificationDisplay | null; unavailable: boolean };
const Context = createContext({
  value: null as NotificationDisplay | null,
  unavailable: false,
  refresh: () => {},
  save: async (value: NotificationDisplay) => value,
});
export const useNotificationPreferences = () => useContext(Context);

export function NotificationPreferencesProvider({ children }: { children: React.ReactNode }) {
  const { user } = useUser();
  const userId = user?.user_id;
  const [state, setState] = useState<State | null>(null);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    if (!userId) return;
    return runWhenPageActive((signal) => {
      void userRequest<NotificationDisplay>("settings/notifications", {
        signal: AbortSignal.any([signal, AbortSignal.timeout(15000)]),
      })
        .then((value) => {
          if (!signal.aborted) setState({ owner: userId, value, unavailable: false });
        })
        .catch(() => {
          if (!signal.aborted)
            setState((previous) => ({
              owner: userId,
              value: previous?.owner === userId ? previous.value : null,
              unavailable: true,
            }));
        });
    });
  }, [userId, revision]);
  useEffect(() => {
    const refresh = () => setRevision((value) => value + 1);
    window.addEventListener(preferencesChanged, refresh);
    return () => window.removeEventListener(preferencesChanged, refresh);
  }, []);
  async function save(value: NotificationDisplay) {
    if (!user) throw new AccountError(401);
    const saved = await userRequest<NotificationDisplay>("settings/notifications", {
      method: "PUT",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": user.csrf_token },
      body: JSON.stringify(value),
    });
    setState({ owner: user.user_id, value: saved, unavailable: false });
    return saved;
  }
  return (
    <Context.Provider
      value={{
        value: state && state.owner === userId ? state.value : null,
        unavailable: Boolean(state && state.owner === userId && state.unavailable),
        refresh: () => setRevision((value) => value + 1),
        save,
      }}
    >
      {children}
    </Context.Provider>
  );
}
