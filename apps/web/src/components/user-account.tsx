"use client";
import { flushSync } from "react-dom";
import { LoadingSkeleton } from "./loading-skeleton";
import { readerLoginLink, prepareReaderSignOut, readerLocation } from "@/lib/reader-runtime";
import { navigationStarted } from "@/lib/reader-navigation";
import { articlePreviewOpened, createPreviewHistory } from "@/lib/reader-preview-history";
import Link from "@/components/reader-link";
import { Bookmark, Hash, UserRound } from "lucide-react";
import {
  Fragment,
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  lazy,
  Suspense,
} from "react";
import { AccountError, userRequest, type UserIdentity, type UserProfile } from "@/lib/user";
import { logOut, saveProfileSettings } from "@/lib/user-mutations";
import { profileLinkFromUrl } from "@/lib/profile-links";
import { ReaderQueryProvider } from "./reader-query-provider";

const UserMenu = lazy(() => import("./user-menu").then((module) => ({ default: module.UserMenu })));

type Session = {
  sessionRevision: number;
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
  sessionRevision: 0,
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

export function UserProvider({
  children,
  refreshKey = 0,
}: {
  children: React.ReactNode;
  refreshKey?: number;
}) {
  const restoreFocus = useRef<HTMLElement | null>(null);
  const [sessionRevision, setSessionRevision] = useState(0);
  const [activityRevision, setActivityRevision] = useState(0);
  const [user, setUser] = useState<UserIdentity | null>(null);
  const confirmedUser = useRef<UserIdentity | null | undefined>(undefined);
  const confirmedRefreshKey = useRef(refreshKey);
  const refreshProfileOnCheck = useRef(false);
  const blockingCheck = useRef(true);
  const authCheck = useRef<AbortController | null>(null);
  const [previewHistory] = useState(createPreviewHistory);
  const [loading, setLoading] = useState(true);
  const [unavailable, setUnavailable] = useState(false);
  const [profileState, setProfileState] = useState<{
    owner: string;
    value: UserProfile | null;
    unavailable: boolean;
  } | null>(null);
  const [profileVersion, setProfileVersion] = useState(0);
  const userId = user?.user_id;
  const profileWrites = useRef(new Set<AbortController>());
  const confirmUser = useCallback(
    (value: UserIdentity | null) => {
      const previous = confirmedUser.current;
      const sameSession =
        previous !== undefined &&
        previous?.user_id === value?.user_id &&
        previous?.csrf_token === value?.csrf_token &&
        previous?.expires_at === value?.expires_at;
      const unchanged =
        sameSession && previous?.name === value?.name && previous?.email === value?.email;
      confirmedUser.current = value;
      if (!sameSession) {
        previewHistory.reset();
        setSessionRevision((revision) => revision + 1);
      }
      if (!unchanged) setUser(value);
      return !sameSession;
    },
    [previewHistory],
  );
  useEffect(() => {
    const pending = profileWrites.current;
    return () => {
      for (const controller of pending) controller.abort();
      pending.clear();
    };
  }, [userId]);
  useLayoutEffect(() => {
    if (loading) return;
    const target = restoreFocus.current;
    restoreFocus.current = null;
    if (target?.isConnected && document.activeElement === document.body)
      target.focus({ preventScroll: true });
  }, [loading]);
  useEffect(() => {
    let checkedAt = Date.now();
    const resume = () => {
      if (document.visibilityState !== "visible" || Date.now() - checkedAt < 60_000) return;
      checkedAt = Date.now();
      refreshProfileOnCheck.current = true;
      setActivityRevision((revision) => revision + 1);
    };
    const restore = (event: Event) => {
      if (event.type === "pageshow" && !(event as PageTransitionEvent).persisted) return;
      checkedAt = Date.now();
      const identity = confirmedUser.current;
      const previewReturn = event.type === "popstate" && previewHistory.restore(readerLocation());
      // This background was already visible beneath a committed preview. Check
      // silently there; ordinary history entries still mask private content.
      const retain =
        previewReturn &&
        !blockingCheck.current &&
        identity !== undefined &&
        (!identity || identity.expires_at > Date.now() / 1000);
      if (!retain) {
        refreshProfileOnCheck.current = true;
        const focused = document.activeElement;
        if (focused instanceof HTMLElement && focused !== document.body)
          restoreFocus.current = focused;
        blockingCheck.current = true;
        flushSync(() => setLoading(true));
      }
      setActivityRevision((revision) => revision + 1);
    };
    const suspend = () => {
      previewHistory.reset();
      blockingCheck.current = true;
      flushSync(() => setLoading(true));
    };
    const navigate = (event: Event) => {
      if (blockingCheck.current || confirmedUser.current === undefined) return;
      const href: unknown = (event as CustomEvent).detail;
      if (typeof href === "string") previewHistory.navigate(readerLocation(), href);
    };
    const commitPreview = () => previewHistory.commit(readerLocation());
    window.addEventListener("pagehide", suspend);
    // A frozen document may outlive its session; the focus throttle must not
    // suppress history restoration checks, even on an immediate Back.
    window.addEventListener("popstate", restore);
    window.addEventListener("pageshow", restore);
    window.addEventListener("focus", resume);
    document.addEventListener("visibilitychange", resume);
    window.addEventListener(navigationStarted, navigate);
    window.addEventListener(articlePreviewOpened, commitPreview);
    return () => {
      window.removeEventListener("pagehide", suspend);
      window.removeEventListener("popstate", restore);
      window.removeEventListener("pageshow", restore);
      window.removeEventListener("focus", resume);
      document.removeEventListener("visibilitychange", resume);
      window.removeEventListener(navigationStarted, navigate);
      window.removeEventListener(articlePreviewOpened, commitPreview);
    };
  }, [previewHistory]);
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
  }, [userId, profileVersion, sessionRevision]);
  async function saveProfile(value: UserProfile) {
    if (!user) throw new AccountError(401);
    const controller = new AbortController();
    profileWrites.current.add(controller);
    try {
      const saved = await saveProfileSettings(
        {
          display_name: value.display_name,
          ...(!value.avatar_variants?.some((variant) => variant.url === value.avatar_url) && {
            avatar_url: value.avatar_url,
          }),
          ...(value.username !== undefined && { username: value.username }),
          ...(value.bio !== undefined && { bio: value.bio }),
          ...(value.location !== undefined && { location: value.location }),
          ...(value.about !== undefined && { about: value.about }),
          ...(value.experience_level !== undefined && { experience_level: value.experience_level }),
          ...(value.links !== undefined && {
            links: value.links
              .filter((link) => link.url.trim())
              .map((link) => profileLinkFromUrl(link.url)),
          }),
          ...(value.stack !== undefined && {
            stack: value.stack.map(({ topic_id, section, since_year }) => ({
              topic_id,
              section,
              since_year,
            })),
          }),
          ...(value.visibility !== undefined && { visibility: value.visibility }),
          ...(value.dev_card !== undefined && { dev_card: value.dev_card }),
        },
        { csrfToken: user.csrf_token, signal: controller.signal },
      );
      if (!controller.signal.aborted)
        setProfileState({ owner: user.user_id, value: saved, unavailable: false });
      return saved;
    } finally {
      profileWrites.current.delete(controller);
    }
  }
  useEffect(() => {
    const controller = new AbortController();
    authCheck.current = controller;
    const expire = () => {
      controller.abort();
      previewHistory.reset();
      confirmUser(null);
      blockingCheck.current = false;
      setLoading(false);
    };
    window.addEventListener("devfeed:user-session-expired", expire);
    userRequest<UserIdentity | null>("auth/me", {
      signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]),
    })
      .then((value) => {
        if (controller.signal.aborted) return;
        const changed = confirmUser(value);
        if (
          value &&
          !changed &&
          (refreshProfileOnCheck.current || confirmedRefreshKey.current !== refreshKey)
        )
          setProfileVersion((revision) => revision + 1);
        refreshProfileOnCheck.current = false;
        confirmedRefreshKey.current = refreshKey;
        setUnavailable(false);
      })
      .catch((error) => {
        if (!controller.signal.aborted) {
          previewHistory.reset();
          confirmUser(null);
          setUnavailable(!(error instanceof AccountError && error.status === 401));
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          blockingCheck.current = false;
          setLoading(false);
        }
      });
    return () => {
      controller.abort();
      if (authCheck.current === controller) authCheck.current = null;
      window.removeEventListener("devfeed:user-session-expired", expire);
    };
  }, [refreshKey, activityRevision, confirmUser, previewHistory]);
  async function signOut() {
    const browser = prepareReaderSignOut();
    try {
      const url = await logOut({ csrfToken: user?.csrf_token ?? "" });
      authCheck.current?.abort();
      blockingCheck.current = false;
      previewHistory.reset();
      confirmUser(null);
      setLoading(false);
      browser.complete(url);
    } catch (error) {
      browser.cancel();
      throw error;
    }
  }
  return (
    <Context.Provider
      value={{
        sessionRevision,
        user,
        loading,
        unavailable,
        signOut,
        saveProfile,
        profile: profileState && profileState.owner === userId ? profileState.value : null,
        profileUnavailable: Boolean(
          profileState && profileState.owner === userId && profileState.unavailable,
        ),
        refreshProfile: () => setProfileVersion((value) => value + 1),
      }}
    >
      <Fragment key={user?.user_id ?? "guest"}>
        <ReaderQueryProvider>{children}</ReaderQueryProvider>
      </Fragment>
    </Context.Provider>
  );
}
export function UserAccount() {
  const { user, loading } = useUser();
  if (user && loading)
    return (
      <span className="user-menu-trigger" aria-busy="true">
        <UserRound size={16} aria-hidden="true" />
      </span>
    );
  if (!user)
    return (
      <a className="header-link account-link" {...readerLoginLink()} aria-label="Sign in">
        <UserRound size={16} aria-hidden="true" />
        <span>Sign in</span>
      </a>
    );
  return (
    <Suspense
      fallback={
        <span className="user-menu-trigger" aria-busy="true">
          <UserRound size={16} aria-hidden="true" />
        </span>
      }
    >
      <UserMenu />
    </Suspense>
  );
}

function AuthorizedContent({
  children,
  loading,
  fallback,
}: {
  children: React.ReactNode;
  loading: boolean;
  fallback?: React.ReactNode;
}) {
  const container = useRef<HTMLDivElement>(null);
  const [height, setHeight] = useState(0);
  useLayoutEffect(() => {
    if (loading) return;
    const element = container.current!;
    const measure = () => setHeight(element.getBoundingClientRect().height);
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [loading]);
  return (
    <div ref={container} style={loading ? { minHeight: height } : undefined}>
      <div hidden={loading} inert={loading}>
        {children}
      </div>
      {loading && (fallback ?? <LoadingSkeleton kind="form" label="Loading your account…" />)}
    </div>
  );
}

export function AccountGate({
  children,
  returnTo,
  loadingFallback,
  title,
  description,
}: {
  children: React.ReactNode;
  returnTo?: string;
  loadingFallback?: React.ReactNode;
  title?: string;
  description?: string;
}) {
  const { user, loading, unavailable } = useUser();
  // Hide personal content while reauthorizing without restarting the retained
  // feed effects when the same account is confirmed.
  if (user)
    return (
      <AuthorizedContent loading={loading} fallback={loadingFallback}>
        {children}
      </AuthorizedContent>
    );
  if (loading)
    return loadingFallback ?? <LoadingSkeleton kind="form" label="Loading your account…" />;
  if (!user)
    return (
      <section className="empty-state">
        <h2>
          {unavailable ? "Sign-in is temporarily unavailable" : (title ?? "Make this feed yours")}
        </h2>
        <p>{description ?? "Sign in to follow sources and topics and personalize your feed."}</p>
        <a className="button primary" {...readerLoginLink(returnTo)}>
          Sign in
        </a>
        <Link className="button" href="/latest">
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
  const { user, loading } = useUser();
  if (!user || loading) return null;
  return (
    <Link
      href="/"
      className={mobile ? undefined : `nav-item ${active ? "active" : ""}`}
      aria-current={active ? "page" : undefined}
    >
      <Hash size={20} />
      {mobile ? "My feed" : <span>My feed</span>}
    </Link>
  );
}

export function ReadLaterNav({
  mobile = false,
  active = false,
}: {
  mobile?: boolean;
  active?: boolean;
}) {
  const { user, loading } = useUser();
  if (!user || loading) return null;
  return (
    <Link
      href="/read-later"
      className={mobile ? undefined : `nav-item ${active ? "active" : ""}`}
      aria-current={active ? "page" : undefined}
    >
      <Bookmark size={20} />
      {mobile ? "Read later" : <span>Read later</span>}
    </Link>
  );
}
