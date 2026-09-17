import { useEffect, useState, type ReactNode } from "react";
import { UserProvider, useUser } from "../../web/src/components/user-account";
import { scheduleSessionExpiry } from "./session-expiry";

const channelName = "devfeed:extension-session";

export function signedOut() {
  const channel = new BroadcastChannel(channelName);
  channel.postMessage("signed-out");
  channel.close();
  // A full reload clears rendered personal data and all provider state.
  window.location.replace("newtab.html#/");
}

function SessionExpiry() {
  const { user } = useUser();
  useEffect(() => {
    if (!user) return;
    return scheduleSessionExpiry(user.expires_at, () => {
      window.dispatchEvent(new Event("devfeed:user-session-expired"));
    });
  }, [user]);
  return null;
}

export function AccountSession({ children }: { children: ReactNode }) {
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let lastRefresh = 0;
    const refresh = () => {
      if (document.visibilityState !== "visible" || Date.now() - lastRefresh < 100) return;
      lastRefresh = Date.now();
      setRevision((value) => value + 1);
    };
    const channel = new BroadcastChannel(channelName);
    // Clear personal data in background new tabs too when another tab signs out.
    channel.onmessage = (event) => {
      if (event.data === "signed-out") setRevision((value) => value + 1);
    };
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      channel.close();
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, []);
  return (
    <UserProvider refreshKey={revision}>
      <SessionExpiry />
      {children}
    </UserProvider>
  );
}
