"use client";

import Image from "next/image";
import Link from "next/link";
import { ServerOff } from "lucide-react";
import brandMark from "@devfeed/theme/assets/devfeed-mark.png";
import { useBrowserTheme } from "@devfeed/ui/browser-theme";
import { RetryButton } from "@devfeed/ui/retry-button";

export default function ErrorPage() {
  useBrowserTheme();

  return (
    <main
      aria-labelledby="partner-error-title"
      className="flex min-h-dvh items-center justify-center px-4 py-12 sm:px-6"
    >
      <div className="w-full max-w-md space-y-8 text-center">
        <Link
          href="/"
          prefetch={false}
          aria-label="DevFeed Partners home"
          className="inline-flex rounded-sm focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-ring"
        >
          <span className="devfeed-brand">
            <Image
              className="devfeed-brand-mark"
              src={brandMark}
              alt=""
              width={40}
              height={40}
              priority
            />
            <span>devfeed.</span>
          </span>
        </Link>
        <section
          aria-labelledby="partner-error-title"
          data-slot="card"
          className="rounded-xl border bg-card px-6 py-8 text-card-foreground shadow-xs sm:px-8"
        >
          <div className="mx-auto mb-5 flex size-12 items-center justify-center rounded-full bg-muted text-muted-foreground">
            <ServerOff size={24} aria-hidden="true" />
          </div>
          <h1 id="partner-error-title" className="text-xl font-semibold tracking-tight">
            Could not load your partner portal
          </h1>
          <p className="mt-3 text-sm leading-6 text-muted-foreground">
            Please try again in a moment. You’ll return to this page with your current filters.
          </p>
          <div className="mt-6">
            {/* Native navigation requests fresh server data and current build assets while
              preserving this page's path and query, even when the client router has failed. */}
            <RetryButton href="" />
          </div>
        </section>
      </div>
    </main>
  );
}
