import Link from "next/link";
export const dynamic = "force-dynamic";
export default async function Login({
  searchParams,
}: {
  searchParams: Promise<{ error?: string }>;
}) {
  const { error } = await searchParams;
  return (
    <main className="login">
      <Link className="brand" href="/">
        DevFeed <span>Partners</span>
      </Link>
      <h1>See what your partnership delivers.</h1>
      <p>
        Track your products and ads, review your partnership tier, and understand your reach on
        DevFeed.
      </p>
      {error && (
        <p role="alert">
          {error === "access_denied"
            ? "Your account needs the partner or superuser role to access this portal."
            : "Sign-in could not be completed. Please try again."}
        </p>
      )}
      {/* OIDC requires a full navigation rather than a prefetched route. */}
      {/* eslint-disable-next-line @next/next/no-html-link-for-pages */}
      <a className="button" href="/api/v1/partner/auth/login">
        Sign in with Zitadel
      </a>
      <p className="muted">Access is provided to members of a DevFeed partner account.</p>
    </main>
  );
}
