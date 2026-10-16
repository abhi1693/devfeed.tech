/** Bounded first-touch data shared by the reader, its extensions, and login handoff. */
export const acquisitionSources = [
  "google",
  "bing",
  "duckduckgo",
  "yahoo",
  "baidu",
  "yandex",
  "github",
  "x",
  "reddit",
  "hacker_news",
  "linkedin",
  "mastodon",
  "bluesky",
  "nick_launches",
  "newsletter",
  "other",
  "direct",
] as const;
export const acquisitionMedia = [
  "organic",
  "referral",
  "social",
  "email",
  "cpc",
  "paid_social",
  "none",
  "unknown",
] as const;
export const acquisitionRoutes = [
  "home",
  "latest",
  "article",
  "topics",
  "topic",
  "sources",
  "source",
  "search",
  "trending",
  "profile",
  "other",
  "unavailable",
] as const;
export type ReaderPlatform = "web" | "chrome_extension" | "edge_extension";
export type ReaderAcquisition = {
  version: 1;
  status: "attributed" | "direct" | "unknown" | "unavailable";
  source: (typeof acquisitionSources)[number] | null;
  medium: (typeof acquisitionMedia)[number] | null;
  campaign: string | null;
  landing_route: (typeof acquisitionRoutes)[number];
  captured_at: number;
  client_platform: ReaderPlatform;
};
export type ReaderMeasurement = ReaderAcquisition & {
  measurement: "eligible" | "unavailable" | "disabled";
  client_id?: string;
  session_id?: number;
};
export const readerMeasurementHeader = "X-Devfeed-Reader-Measurement";
export const acquisitionStorageKey = "devfeed:reader-acquisition:v1";
const maxAge = 30 * 86400;
const maxBytes = 1536;
const token = /^[a-z][a-z0-9_-]{0,63}$/;
const client = /^\d{1,10}\.\d{1,10}$/;
const unixNow = () => Math.floor(Date.now() / 1000);
const includes = (values: readonly string[], value: unknown): value is string =>
  typeof value === "string" && values.includes(value);

export function acquisitionCampaigns(value: unknown): string[] {
  return Array.isArray(value)
    ? [
        ...new Set(
          value.filter((item): item is string => typeof item === "string" && token.test(item)),
        ),
      ].slice(0, 100)
    : [];
}
export function acquisitionRoute(path: string): ReaderAcquisition["landing_route"] {
  const clean = path.split(/[?#]/)[0];
  if (clean === "/") return "home";
  for (const route of ["latest", "topics", "sources", "search", "trending"] as const)
    if (clean === `/${route}`) return route;
  if (/^\/articles\/[^/]+$/.test(clean)) return "article";
  if (/^\/topics\/[^/]+(?:\/[^/]+)?$/.test(clean)) return "topic";
  if (/^\/sources\/[^/]+(?:\/[^/]+)?$/.test(clean)) return "source";
  if (/^\/users\/[^/]+$/.test(clean)) return "profile";
  return "other";
}
export function unavailableAcquisition(
  platform: ReaderPlatform,
  now = unixNow(),
): ReaderAcquisition {
  return {
    version: 1,
    status: "unavailable",
    source: null,
    medium: null,
    campaign: null,
    landing_route: "unavailable",
    captured_at: now,
    client_platform: platform,
  };
}
const referrers: Record<string, [ReaderAcquisition["source"], ReaderAcquisition["medium"]]> = {
  "google.com": ["google", "organic"],
  "www.google.com": ["google", "organic"],
  "bing.com": ["bing", "organic"],
  "www.bing.com": ["bing", "organic"],
  "duckduckgo.com": ["duckduckgo", "organic"],
  "search.yahoo.com": ["yahoo", "organic"],
  "baidu.com": ["baidu", "organic"],
  "www.baidu.com": ["baidu", "organic"],
  "yandex.com": ["yandex", "organic"],
  "yandex.ru": ["yandex", "organic"],
  "github.com": ["github", "referral"],
  "t.co": ["x", "social"],
  "x.com": ["x", "social"],
  "twitter.com": ["x", "social"],
  "www.reddit.com": ["reddit", "social"],
  "reddit.com": ["reddit", "social"],
  "news.ycombinator.com": ["hacker_news", "referral"],
  "www.linkedin.com": ["linkedin", "social"],
  "linkedin.com": ["linkedin", "social"],
  "bsky.app": ["bluesky", "social"],
  "nicklaunches.com": ["nick_launches", "referral"],
};
export function captureReaderAcquisition({
  url,
  referrer,
  platform,
  campaigns = [],
  now = unixNow(),
}: {
  url: URL;
  referrer: string;
  platform: ReaderPlatform;
  campaigns?: readonly string[];
  now?: number;
}): ReaderAcquisition {
  const base = {
    ...unavailableAcquisition(platform, now),
    landing_route: acquisitionRoute(url.pathname),
  };
  // A login/callback page is never evidence of an initial landing.
  if (
    url.pathname === "/login" ||
    url.pathname.startsWith("/api/") ||
    url.pathname.startsWith("/extension/")
  )
    return unavailableAcquisition(platform, now);
  const sourceInput = url.searchParams.get("utm_source")?.toLowerCase();
  const source =
    sourceInput === "twitter"
      ? "x"
      : sourceInput === "nicklaunches"
        ? "nick_launches"
        : sourceInput;
  const medium = url.searchParams.get("utm_medium")?.toLowerCase();
  const campaignInput = url.searchParams.get("utm_campaign");
  const campaign =
    campaignInput && campaigns.includes(campaignInput) && token.test(campaignInput)
      ? campaignInput
      : null;
  if (sourceInput || medium || campaignInput) {
    if (
      includes(acquisitionSources, source) &&
      source !== "direct" &&
      includes(acquisitionMedia, medium) &&
      !["none", "unknown"].includes(medium)
    )
      return {
        ...base,
        status: "attributed",
        source: source as ReaderAcquisition["source"],
        medium: medium as ReaderAcquisition["medium"],
        campaign,
      };
    return { ...base, status: "unknown", medium: "unknown" };
  }
  if (!referrer) return { ...base, status: "direct", source: "direct", medium: "none" };
  try {
    const previous = new URL(referrer);
    if (["http:", "https:"].includes(previous.protocol) && previous.origin !== url.origin) {
      const known = referrers[previous.hostname.toLowerCase()];
      if (known) return { ...base, status: "attributed", source: known[0], medium: known[1] };
    }
  } catch {
    /* An unreadable referrer provides no attribution evidence. */
  }
  return { ...base, status: "unknown", medium: "unknown" };
}

export function sanitizeReaderMeasurement(
  value: unknown,
  campaigns: readonly string[] = [],
  now = unixNow(),
): ReaderMeasurement | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const data = value as Record<string, unknown>;
  if (
    Object.keys(data).some(
      (key) =>
        ![
          "version",
          "status",
          "source",
          "medium",
          "campaign",
          "landing_route",
          "captured_at",
          "client_platform",
          "measurement",
          "client_id",
          "session_id",
        ].includes(key),
    ) ||
    data.version !== 1 ||
    !includes(["attributed", "direct", "unknown", "unavailable"], data.status) ||
    !includes(["web", "chrome_extension", "edge_extension"], data.client_platform) ||
    !includes(acquisitionRoutes, data.landing_route) ||
    !Number.isSafeInteger(data.captured_at) ||
    Number(data.captured_at) < now - maxAge ||
    Number(data.captured_at) > now + 300 ||
    !includes(["eligible", "unavailable", "disabled"], data.measurement) ||
    !(data.source === null || includes(acquisitionSources, data.source)) ||
    !(data.medium === null || includes(acquisitionMedia, data.medium)) ||
    !(
      data.campaign === null ||
      (typeof data.campaign === "string" &&
        token.test(data.campaign) &&
        campaigns.includes(data.campaign))
    )
  )
    return null;
  if (
    (data.status === "direct" &&
      (data.source !== "direct" || data.medium !== "none" || data.campaign !== null)) ||
    (data.status === "unknown" &&
      (data.source !== null || data.medium !== "unknown" || data.campaign !== null)) ||
    (data.status === "unavailable" &&
      (data.source !== null ||
        data.medium !== null ||
        data.campaign !== null ||
        data.landing_route !== "unavailable")) ||
    (data.status === "attributed" &&
      (!data.source ||
        data.source === "direct" ||
        !data.medium ||
        data.medium === "none" ||
        data.medium === "unknown"))
  )
    return null;
  if (data.measurement === "eligible") {
    if (
      typeof data.client_id !== "string" ||
      !client.test(data.client_id) ||
      !Number.isSafeInteger(data.session_id) ||
      Number(data.session_id) <= 0
    )
      return null;
  } else if (data.client_id !== undefined || data.session_id !== undefined) return null;
  const result = { ...data } as ReaderMeasurement;
  return JSON.stringify(result).length <= maxBytes ? result : null;
}

function acquisitionOnly(value: ReaderAcquisition): ReaderAcquisition {
  return {
    version: 1,
    status: value.status,
    source: value.source,
    medium: value.medium,
    campaign: value.campaign,
    landing_route: value.landing_route,
    captured_at: value.captured_at,
    client_platform: value.client_platform,
  };
}

type Runtime = {
  enabled: boolean;
  platform: ReaderPlatform;
  gaId?: string;
  extensionEnabled: boolean;
  campaigns: readonly string[];
  acquisition: ReaderAcquisition;
  storage: boolean;
  identity?: { client_id: string; session_id: number };
  identityAt?: number;
  currentIdentity?: () => { client_id: string; session_id: number } | null;
  stopped: boolean;
  revision: number;
};
let runtime: Runtime | undefined;
let loginActive = false;
function cookieName() {
  return location.protocol === "https:"
    ? "__Host-devfeed_user_acquisition"
    : "devfeed_user_acquisition";
}
function clearCookie() {
  if (location.protocol === "chrome-extension:") return;
  for (const name of ["devfeed_user_acquisition", "__Host-devfeed_user_acquisition"])
    document.cookie = `${name}=; Max-Age=0; Path=/; SameSite=Lax${location.protocol === "https:" ? "; Secure" : ""}`;
}
export function readerMeasurementPermitted(gaId?: string) {
  try {
    const privacy = navigator as Navigator & { globalPrivacyControl?: boolean };
    return (
      navigator.doNotTrack !== "1" &&
      !privacy.globalPrivacyControl &&
      !(gaId && (window as unknown as Record<string, unknown>)[`ga-disable-${gaId}`]) &&
      localStorage.getItem("devfeed:extension-analytics-disabled") !== "true"
    );
  } catch {
    return false;
  }
}
export function clearReaderAcquisition() {
  if (runtime) {
    runtime.identity = undefined;
    runtime.identityAt = undefined;
    runtime.revision++;
    runtime.acquisition = unavailableAcquisition(runtime.platform);
  }
  try {
    sessionStorage.removeItem(acquisitionStorageKey);
  } catch {
    /* Storage is optional. */
  }
  clearCookie();
}
function permitted(current: Runtime) {
  if (
    !current.enabled ||
    current.stopped ||
    !current.storage ||
    !readerMeasurementPermitted(current.gaId)
  ) {
    clearReaderAcquisition();
    return false;
  }
  return true;
}
export function currentReaderMeasurement(): ReaderMeasurement | null {
  const current = runtime;
  if (!current) return null;
  const allowed = permitted(current);
  const cached =
    current.identityAt && Date.now() - current.identityAt <= 60_000 ? current.identity : undefined;
  const identity = allowed ? (current.currentIdentity?.() ?? cached) : undefined;
  const validIdentity =
    identity &&
    client.test(identity.client_id) &&
    Number.isSafeInteger(identity.session_id) &&
    identity.session_id > 0;
  return {
    ...current.acquisition,
    measurement: !allowed ? "disabled" : validIdentity ? "eligible" : "unavailable",
    ...(validIdentity && { client_id: identity.client_id, session_id: identity.session_id }),
  };
}
function publishLoginCookie() {
  if (!loginActive || !runtime || location.protocol === "chrome-extension:") return;
  const measurement = currentReaderMeasurement();
  if (!measurement || measurement.measurement === "disabled") {
    clearCookie();
    return;
  }
  const value = encodeURIComponent(JSON.stringify(measurement));
  if (value.length > maxBytes) {
    clearCookie();
    return;
  }
  document.cookie = `${cookieName()}=${value}; Max-Age=600; Path=/; SameSite=Lax${location.protocol === "https:" ? "; Secure" : ""}`;
}
export function startReaderAcquisition({
  enabled,
  platform = "web",
  gaId,
  campaigns = [],
  url = new URL(location.href),
  referrer = document.referrer,
  currentIdentity,
  extensionEnabled = false,
}: {
  enabled: boolean;
  platform?: ReaderPlatform;
  gaId?: string;
  campaigns?: readonly string[];
  url?: URL;
  referrer?: string;
  currentIdentity?: Runtime["currentIdentity"];
  extensionEnabled?: boolean;
}) {
  const current: Runtime = {
    enabled,
    platform,
    gaId,
    extensionEnabled,
    campaigns,
    acquisition: unavailableAcquisition(platform),
    storage: false,
    currentIdentity,
    stopped: false,
    revision: 0,
  };
  runtime = current;
  if (enabled && readerMeasurementPermitted(gaId)) {
    try {
      const saved = sessionStorage.getItem(acquisitionStorageKey);
      const stored =
        saved &&
        sanitizeReaderMeasurement({ ...JSON.parse(saved), measurement: "unavailable" }, campaigns);
      current.acquisition =
        stored && stored.client_platform === platform
          ? acquisitionOnly(stored)
          : captureReaderAcquisition({ url, referrer, platform, campaigns });
      sessionStorage.setItem(acquisitionStorageKey, JSON.stringify(current.acquisition));
      current.storage = true;
    } catch {
      current.acquisition = unavailableAcquisition(platform);
    }
  } else clearReaderAcquisition();
  const updateLoginLink = (event: Event) => {
    if (current.platform === "web" || !(event.target instanceof Element)) return;
    const anchor = event.target.closest<HTMLAnchorElement>("a[href]");
    if (!anchor) return;
    try {
      const target = new URL(anchor.href);
      if (target.origin === "https://devfeed.tech" && target.pathname === "/login")
        anchor.hash = readerAcquisitionFragment();
    } catch {
      /* Ignore unrelated or malformed navigation. */
    }
  };
  document.addEventListener("click", updateLoginLink, true);
  document.addEventListener("pointerdown", updateLoginLink, true);
  document.addEventListener("focusin", updateLoginLink, true);
  const privacy = () => {
    permitted(current);
    publishLoginCookie();
  };
  window.addEventListener("storage", privacy);
  window.addEventListener("focus", privacy);
  window.addEventListener("devfeed:measurement-privacy-changed", privacy);
  publishLoginCookie();
  return () => {
    current.stopped = true;
    document.removeEventListener("click", updateLoginLink, true);
    document.removeEventListener("pointerdown", updateLoginLink, true);
    document.removeEventListener("focusin", updateLoginLink, true);
    window.removeEventListener("storage", privacy);
    window.removeEventListener("focus", privacy);
    window.removeEventListener("devfeed:measurement-privacy-changed", privacy);
    if (runtime === current) runtime = undefined;
  };
}

/** Ask an already deferred Google tag for its existing identifiers; never initialize one. */
export function refreshReaderMeasurement(done?: () => void) {
  const current = runtime;
  if (!current || current.platform !== "web" || !current.gaId || !permitted(current)) {
    done?.();
    return;
  }
  const gtag = (window as Window & { gtag?: (...args: unknown[]) => void }).gtag;
  if (!gtag || !document.querySelector(`script[data-devfeed-ga="${current.gaId}"]`)) {
    done?.();
    return;
  }
  const revision = ++current.revision;
  let clientId: unknown;
  let sessionId: unknown;
  const accept = () => {
    if (runtime !== current || current.revision !== revision || !permitted(current)) return;
    const id =
      typeof sessionId === "string" && /^\d+$/.test(sessionId) ? Number(sessionId) : sessionId;
    if (
      typeof clientId === "string" &&
      client.test(clientId) &&
      Number.isSafeInteger(id) &&
      Number(id) > 0
    ) {
      current.identity = { client_id: clientId, session_id: Number(id) };
      current.identityAt = Date.now();
      publishLoginCookie();
      done?.();
    }
  };
  try {
    gtag("get", current.gaId, "client_id", (value: unknown) => {
      clientId = value;
      accept();
    });
    gtag("get", current.gaId, "session_id", (value: unknown) => {
      sessionId = value;
      accept();
    });
  } catch {
    /* An unavailable Google tag cannot block signup or article opens. */
    done?.();
  }
}

/** Extension login context stays in the fragment, away from HTTP URLs and referrers. */
export function readerAcquisitionFragment() {
  const value = currentReaderMeasurement();
  return value ? `#acquisition=${encodeURIComponent(JSON.stringify(value))}` : "";
}
export function prepareReaderLoginAcquisition() {
  loginActive = true;
  if (location.hash.startsWith("#acquisition=")) {
    const encoded = location.hash.slice("#acquisition=".length);
    // Clear even malformed fragments before the identity-provider navigation.
    history.replaceState(history.state, "", `${location.pathname}${location.search}`);
    if (runtime && encoded.length <= maxBytes && readerMeasurementPermitted(runtime.gaId)) {
      try {
        const handoff = sanitizeReaderMeasurement(
          JSON.parse(decodeURIComponent(encoded)),
          runtime.campaigns,
        );
        if (handoff && handoff.client_platform !== "web") {
          if (!runtime.extensionEnabled || handoff.measurement === "disabled") {
            runtime.enabled = false;
            clearReaderAcquisition();
            return () => {
              loginActive = false;
            };
          }
          // The extension's independent analytics permission may be enabled while web GA is off.
          // Probe optional storage without retaining the extension's identifiers there.
          try {
            sessionStorage.setItem(`${acquisitionStorageKey}:available`, "true");
            sessionStorage.removeItem(`${acquisitionStorageKey}:available`);
          } catch {
            clearReaderAcquisition();
            return () => {
              loginActive = false;
            };
          }
          runtime.enabled = true;
          runtime.storage = true;
          runtime.platform = handoff.client_platform;
          const { client_id, session_id } = handoff;
          runtime.acquisition = acquisitionOnly(handoff);
          runtime.identity = client_id && session_id ? { client_id, session_id } : undefined;
          runtime.identityAt = runtime.identity ? Date.now() : undefined;
        }
      } catch {
        /* Malformed attribution never interferes with sign-in. */
      }
    }
  }
  refreshReaderMeasurement();
  publishLoginCookie();
  return () => {
    loginActive = false;
  };
}
