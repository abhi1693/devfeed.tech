"use client";

import Image from "next/image";
import Link from "next/link";
import brandMark from "@devfeed/theme/assets/devfeed-mark.png";
import { SignIn, type SignInProvider } from "@devfeed/ui/sign-in";
import { LoginFeedback } from "@/components/molecules/login-feedback";

export function LoginPanel({
  enabled,
  providers = [],
  error,
  signedOut = false,
}: {
  enabled: boolean;
  providers?: SignInProvider[];
  error?: string;
  signedOut?: boolean;
}) {
  const freshSignIn = Boolean(error) || signedOut;
  function loginHref(provider: SignInProvider | null) {
    const params = new URLSearchParams();
    if (provider) params.set("provider", provider);
    if (freshSignIn) params.set("reauthenticate", "true");
    return `/api/v1/admin/auth/login${params.size ? `?${params}` : ""}`;
  }
  return (
    <>
      <LoginFeedback error={error} signedOut={signedOut} />
      <SignIn
        config={{ enabled, providers }}
        title="Sign in to DevFeed Admin"
        error={error}
        loginHref={loginHref}
        hostedLabel={error ? "Sign in again" : "Continue to sign in"}
        unavailableMessage="Sign-in is not available. Please contact your administrator."
        brand={
          <Link href="/" prefetch={false} aria-label="DevFeed Admin home">
            <Image src={brandMark} alt="" width={34} height={34} priority />
            <span>
              devfeed<span>.</span>
            </span>
          </Link>
        }
        notice={
          <>
            {enabled && error && (
              <p>
                Try again after your access is updated, or use another account. A fresh sign-in will
                be requested.
              </p>
            )}
            <p>
              Access requires the administrator role in your organization. No public registration.
            </p>
          </>
        }
        footer={<span>© {new Date().getFullYear()} DevFeed</span>}
      />
    </>
  );
}
