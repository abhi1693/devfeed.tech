// The website uses its own origin and fetch. Bundled clients supply their transport
// once at startup; presentation components stay identical on both platforms.
import type { Article } from "./types";

type ReaderRuntime = {
  request: typeof fetch;
  publicOrigin: string;
  location?: () => URL;
  signedOut?: () => void;
  reload?: (href: string) => void;
  rememberArticles?: (articles: Article[]) => void;
};

let runtime: ReaderRuntime | undefined;

export function configureReaderRuntime(value: ReaderRuntime) {
  runtime = value;
}

export const readerRequest: typeof fetch = (input, init) =>
  runtime ? runtime.request(input, init) : fetch(input, init);

export function readerPublicOrigin() {
  return runtime?.publicOrigin ?? window.location.origin;
}

export function readerLocation() {
  return runtime?.location?.() ?? new URL(window.location.href);
}

export function readerSignedOut() {
  if (runtime?.signedOut) runtime.signedOut();
  // eslint-disable-next-line @next/next/no-location-assign-relative-destination -- Sign-out must clear the router cache and rendered personal data.
  else window.location.assign("/");
}

export function rememberReaderArticles(articles: Article[]) {
  runtime?.rememberArticles?.(articles);
}

export function readerReload(href: string) {
  if (runtime?.reload) runtime.reload(href);
  else window.location.assign(href);
}

export function readerWebsiteLink(href: string) {
  return runtime
    ? {
        href: new URL(href, runtime.publicOrigin).href,
        target: "_blank",
        rel: "noopener noreferrer",
      }
    : { href };
}

/**
 * Keep extension sign-in in its short-lived browser tab. Website users return
 * to their requested page; bundled readers return to a completion page that
 * closes the tab and lets focus refresh their session.
 */
export function readerLoginLink(returnTo?: string, { register = false } = {}) {
  const params = new URLSearchParams();
  if (register) params.set("register", "true");
  params.set("return_to", runtime ? "/extension/login-complete" : (returnTo ?? "/"));
  return readerWebsiteLink(`/api/v1/user/auth/login?${params}`);
}
