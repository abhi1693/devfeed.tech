"use client";

import Image from "next/image";
import Link from "next/link";
import { useEffect, useState } from "react";
import brandMark from "@devfeed/theme/assets/devfeed-mark.png";
import styles from "./user-login.module.css";

type Provider = "github" | "google";
type AuthConfig = { enabled: boolean; providers: Provider[] };

function loginHref(provider: Provider | null, returnTo: string, register: boolean) {
  const params = new URLSearchParams();
  if (provider) params.set("provider", provider);
  params.set("return_to", returnTo);
  if (register) params.set("register", "true");
  return `/api/v1/user/auth/login?${params}`;
}

function ProviderIcon({ provider }: { provider: Provider }) {
  if (provider === "github")
    return (
      <svg aria-hidden="true" viewBox="0 0 24 24" fill="currentColor">
        <path d="M12 .9a11.1 11.1 0 0 0-3.51 21.63c.56.1.76-.24.76-.54v-2.13c-3.1.68-3.75-1.32-3.75-1.32-.5-1.28-1.24-1.62-1.24-1.62-1.01-.7.08-.69.08-.69 1.12.08 1.71 1.15 1.71 1.15 1 1.71 2.61 1.22 3.24.93.1-.72.39-1.22.71-1.5-2.48-.28-5.09-1.24-5.09-5.52 0-1.22.44-2.22 1.15-3-.12-.28-.5-1.42.11-2.96 0 0 .94-.3 3.06 1.15a10.65 10.65 0 0 1 5.57 0c2.12-1.44 3.05-1.15 3.05-1.15.61 1.54.23 2.68.12 2.96.71.78 1.14 1.78 1.14 3.01 0 4.29-2.61 5.23-5.1 5.51.4.35.76 1.02.76 2.07v3.11c0 .3.2.65.77.54A11.1 11.1 0 0 0 12 .9Z" />
      </svg>
    );
  return (
    <svg aria-hidden="true" viewBox="0 0 48 48">
      <path
        fill="#4285f4"
        d="M43.6 24.5c0-1.4-.1-2.8-.4-4.1H24v7.8h11a9.4 9.4 0 0 1-4.1 6.2v5h6.6c3.9-3.6 6.1-8.8 6.1-14.9Z"
      />
      <path
        fill="#34a853"
        d="M24 44c5.5 0 10.1-1.8 13.5-4.8l-6.6-5c-1.8 1.2-4.1 2-6.9 2-5.3 0-9.8-3.6-11.4-8.4H5.8v5.2A20 20 0 0 0 24 44Z"
      />
      <path fill="#fbbc05" d="M12.6 27.8a12 12 0 0 1 0-7.6V15H5.8a20 20 0 0 0 0 17.9l6.8-5.1Z" />
      <path
        fill="#ea4335"
        d="M24 11.8c3 0 5.6 1 7.7 3.1l5.8-5.8C34 5.8 29.5 4 24 4A20 20 0 0 0 5.8 15l6.8 5.2c1.6-4.8 6.1-8.4 11.4-8.4Z"
      />
    </svg>
  );
}

export function UserLogin({
  returnTo = "/",
  register = false,
  error = false,
}: {
  returnTo?: string;
  register?: boolean;
  error?: boolean;
}) {
  const [config, setConfig] = useState<AuthConfig | null>(null);
  const [unavailable, setUnavailable] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    fetch("/api/v1/user/auth/config", { cache: "no-store", signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error("Authentication configuration unavailable");
        return (await response.json()) as AuthConfig;
      })
      .then(setConfig)
      .catch(() => {
        if (!controller.signal.aborted) setUnavailable(true);
      });
    return () => controller.abort();
  }, []);

  const providers = config?.providers ?? [];
  return (
    <main className={styles.page}>
      <section className={styles.card} aria-labelledby="login-title">
        <Link href="/latest" className={styles.brand} aria-label="DevFeed home">
          <Image src={brandMark} alt="" width={34} height={34} priority />
          <span>
            devfeed<span className={styles.brandDot}>.</span>
          </span>
        </Link>
        <h1 id="login-title">{register ? "Join DevFeed" : "Sign in to DevFeed"}</h1>
        <p className={styles.subtitle}>
          {register
            ? "Choose how you’d like to get started."
            : "Welcome back. Pick up where you left off."}
        </p>

        {error && (
          <p className={styles.error} role="alert">
            Sign-in didn’t finish. Choose a provider to try again.
          </p>
        )}

        {providers.length > 0 ? (
          <div className={styles.providers}>
            {providers.map((provider) => (
              <a
                className={styles.provider}
                href={loginHref(provider, returnTo, register)}
                key={provider}
              >
                <ProviderIcon provider={provider} />
                <span>Continue with {provider === "github" ? "GitHub" : "Google"}</span>
              </a>
            ))}
          </div>
        ) : unavailable || (config && !config.enabled) ? (
          <p className={styles.unavailable} role="status">
            Sign-in is temporarily unavailable. You can keep reading without an account.
          </p>
        ) : config ? (
          <a className={styles.provider} href={loginHref(null, returnTo, register)}>
            Continue to sign in
          </a>
        ) : (
          <p className={styles.loading} role="status">
            Checking sign-in options…
          </p>
        )}

        <p className={styles.terms}>
          By continuing, you agree to our <Link href="/legal/terms">Terms</Link> and{" "}
          <Link href="/legal/privacy">Privacy Policy</Link>.
        </p>
        <div className={styles.footer}>
          {register ? (
            <>
              Already have an account?{" "}
              <Link href={`/login?return_to=${encodeURIComponent(returnTo)}`}>Sign in</Link>
            </>
          ) : (
            <>
              New to DevFeed?{" "}
              <Link href={`/login?register=true&return_to=${encodeURIComponent(returnTo)}`}>
                Create an account
              </Link>
            </>
          )}
        </div>
      </section>
      <Link href="/latest" className={styles.back}>
        Back to the feed
      </Link>
    </main>
  );
}
