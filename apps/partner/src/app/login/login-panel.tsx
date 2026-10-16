"use client";

import Image from "next/image";
import Link from "next/link";
import brandMark from "@devfeed/theme/assets/devfeed-mark.png";
import { SignIn, type SignInConfig, type SignInProvider } from "@devfeed/ui/sign-in";

export function LoginPanel({
  config,
  unavailable,
  error,
  signedOut = false,
}: {
  config: SignInConfig | null;
  unavailable?: boolean;
  error?: string;
  signedOut?: boolean;
}) {
  function loginHref(provider: SignInProvider | null) {
    const params = new URLSearchParams();
    if (provider) params.set("provider", provider);
    if (error || signedOut) params.set("reauthenticate", "true");
    return `/api/v1/partner/auth/login${params.size ? `?${params}` : ""}`;
  }
  return (
    <div className="partner-login">
      <SignIn
        config={config}
        unavailable={unavailable}
        title="Sign in to DevFeed Partners"
        error={error}
        loginHref={loginHref}
        hostedLabel={error ? "Sign in again" : "Continue to sign in"}
        unavailableMessage="Partner sign-in is not available. Please try again shortly."
        brand={
          <Link href="/" prefetch={false} aria-label="DevFeed Partners home">
            <Image src={brandMark} alt="" width={34} height={34} priority />
            <span>
              devfeed<span>.</span>
            </span>
          </Link>
        }
        notice={<p>Access is provided to members of a DevFeed partner account.</p>}
        footer={<span>© {new Date().getFullYear()} DevFeed</span>}
      />
    </div>
  );
}
