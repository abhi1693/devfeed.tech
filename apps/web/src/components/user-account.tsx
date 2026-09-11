"use client";
/* eslint-disable @next/next/no-html-link-for-pages -- Authentication needs a full browser redirect. */
import Link from "next/link";
import { Hash, UserRound } from "lucide-react";
import { createContext, useContext, useEffect, useState } from "react";
import { UserMenu } from "./user-menu";
import {
  AccountError,
  userRequest,
  type UserIdentity,
  type UserProfile,
} from "@/lib/user";

type Session = {
  user: UserIdentity | null;
  loading: boolean;
  unavailable: boolean;
  signOut: () => Promise<void>;
  profile: UserProfile | null;
  profileUnavailable: boolean;
  refreshProfile: () => void;
  saveProfile: (value: UserProfile) => Promise<UserProfile>;
};
const Context = createContext<Session>({
  user: null,
  loading: true,
  unavailable: false,
  signOut: async () => {},
  profile: null,
  profileUnavailable: false,
  refreshProfile: () => {},
  saveProfile: async (value) => value,
});
export const useUser = () => useContext(Context);

export function UserProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<UserIdentity | null>(null);
  const [loading, setLoading] = useState(true);
  const [unavailable, setUnavailable] = useState(false);
  const [profileState, setProfileState] = useState<{
    owner: string;
    value: UserProfile | null;
    unavailable: boolean;
  } | null>(null);
  const [profileVersion, setProfileVersion] = useState(0);
  const userId = user?.user_id;
  useEffect(() => {
    if (!userId) return;
    const controller = new AbortController();
    userRequest<UserProfile>("settings/profile", {
      signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]),
    })
      .then((value) => {
        if (!controller.signal.aborted)
          setProfileState({ owner: userId, value, unavailable: false });
      })
      .catch(() => {
        if (!controller.signal.aborted)
          setProfileState({ owner: userId, value: null, unavailable: true });
      });
    return () => controller.abort();
  }, [userId, profileVersion]);
  async function saveProfile(value: UserProfile) {
    if (!user) throw new AccountError(401);
    const saved = await userRequest<UserProfile>("settings/profile", {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": user.csrf_token,
      },
      body: JSON.stringify(value),
    });
    setProfileState({ owner: user.user_id, value: saved, unavailable: false });
    return saved;
  }
  useEffect(() => {
    const controller = new AbortController();
    const expire = () => setUser(null);
    window.addEventListener("devfeed:user-session-expired", expire);
    userRequest<UserIdentity | null>("auth/me", {
      signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]),
    })
      .then(setUser)
      .catch((error) => {
        if (!controller.signal.aborted)
          setUnavailable(
            !(error instanceof AccountError && error.status === 401),
          );
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => {
      controller.abort();
      window.removeEventListener("devfeed:user-session-expired", expire);
    };
  }, []);
  async function signOut() {
    await userRequest("auth/logout", {
      method: "POST",
      headers: { "X-CSRF-Token": user?.csrf_token ?? "" },
    });
    setUser(null);
    // Clear all rendered personal data and the client router cache on sign-out.
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination
    window.location.assign("/");
  }
  return (
    <Context.Provider
      value={{
        user,
        loading,
        unavailable,
        signOut,
        saveProfile,
        profile:
          profileState && profileState.owner === userId
            ? profileState.value
            : null,
        profileUnavailable: Boolean(
          profileState &&
          profileState.owner === userId &&
          profileState.unavailable,
        ),
        refreshProfile: () => setProfileVersion((value) => value + 1),
      }}
    >
      {children}
    </Context.Provider>
  );
}
export function UserAccount() {
  const { user } = useUser();
  if (!user)
    return (
      <a
        className="header-link account-link"
        href="/api/v1/user/auth/login"
        aria-label="Sign in"
      >
        <UserRound size={16} aria-hidden="true" />
        <span>Sign in</span>
      </a>
    );
  return <UserMenu />;
}

export function AccountGate({
  children,
  returnTo,
}: {
  children: React.ReactNode;
  returnTo?: string;
}) {
  const { user, loading, unavailable } = useUser();
  if (loading) return <p role="status">Loading your account…</p>;
  if (!user)
    return (
      <section className="empty-state">
        <h2>
          {unavailable
            ? "Sign-in is temporarily unavailable"
            : "Make this feed yours"}
        </h2>
        <p>Sign in to save topics and personalize your feed.</p>
        <a
          className="button primary"
          href={
            returnTo
              ? `/api/v1/user/auth/login?return_to=${encodeURIComponent(returnTo)}`
              : "/api/v1/user/auth/login"
          }
        >
          Sign in
        </a>
        <Link className="button" href="/">
          Browse latest articles
        </Link>
      </section>
    );
  return children;
}

export function PersonalFeedNav({
  mobile = false,
  active = false,
}: {
  mobile?: boolean;
  active?: boolean;
}) {
  const { user } = useUser();
  if (!user) return null;
  return (
    <Link
      href="/my-feed"
      className={mobile ? undefined : `nav-item ${active ? "active" : ""}`}
      aria-current={active ? "page" : undefined}
    >
      <Hash size={20} />
      {mobile ? "My feed" : <span>My feed</span>}
    </Link>
  );
}
