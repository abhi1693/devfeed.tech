"use client";

import { signInFailure } from "@devfeed/ui/sign-in-feedback";

import Image from "next/image";
import Link from "@/components/reader-link";
import { useEffect, useState } from "react";
import brandMark from "@devfeed/theme/assets/devfeed-mark.png";
import { SignIn, type SignInConfig, type SignInProvider } from "@devfeed/ui/sign-in";
import { readerReturnDestination, type ReaderDestination } from "@/lib/reader-destination";
import type { ReaderDestinationRecord } from "@devfeed/http/reader-destinations";

function loginHref(
  provider: SignInProvider | null,
  returnTo: string | ReaderDestination | ReaderDestinationRecord,
) {
  const params = new URLSearchParams();
  if (provider) params.set("provider", provider);
  params.set("return_to", readerReturnDestination(returnTo));
  return `/api/v1/user/auth/login?${params}`;
}

export function UserLogin({
  returnTo = "/",
  error = false,
  retry,
}: {
  returnTo?: string | ReaderDestination | ReaderDestinationRecord;
  error?: boolean | string;
  retry?: string;
}) {
  const [config, setConfig] = useState<SignInConfig | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    fetch("/api/v1/user/auth/config", { cache: "no-store", signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error("Authentication configuration unavailable");
        return (await response.json()) as SignInConfig;
      })
      .then(setConfig)
      .catch(() => {
        if (!controller.signal.aborted) setUnavailable(true);
      });
    return () => controller.abort();
  }, []);

  return (
    <SignIn
      config={config}
      unavailable={unavailable}
      error={signInFailure(error)}
      retry={Boolean(error)}
      loginHref={(provider) => {
        if (!retry) return loginHref(provider, returnTo);
        const params = new URLSearchParams({ retry });
        if (provider) params.set("provider", provider);
        return `/api/v1/user/auth/login?${params}`;
      }}
      unavailableMessage="Sign-in is temporarily unavailable. You can keep reading without an account."
      brand={
        <Link href="/latest" aria-label="DevFeed home">
          <Image src={brandMark} alt="" width={34} height={34} priority />
          <span>
            devfeed<span>.</span>
          </span>
        </Link>
      }
      footer={
        <>
          <span>© {new Date().getFullYear()} DevFeed</span>
          <nav aria-label="Legal">
            <Link href="/legal/privacy">Privacy</Link>
            <Link href="/legal/terms">Terms</Link>
          </nav>
        </>
      }
    />
  );
}
