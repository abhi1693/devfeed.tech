"use client";

import { RetryButton } from "@devfeed/ui/retry-button";
import { useEffect, useRef } from "react";
import { notify } from "@/lib/notifications";
import { PageTitle } from "@/components/molecules/page-title";

export default function ErrorPage() {
  const notified = useRef(false);
  useEffect(() => {
    if (notified.current) return;
    const timer = setTimeout(() => {
      notified.current = true;
      notify.error("Could not load this page", { description: "Check the service connection and try again.", id: "page-error" });
    }, 0);
    return () => clearTimeout(timer);
  }, []);
  return <main className="mx-auto max-w-lg space-y-4 px-5 py-20">
    <PageTitle title="Admin service unavailable" />
    <h1 className="text-xl font-semibold">Admin service unavailable</h1>
    <p className="text-sm text-muted-foreground">Could not load this page. Check the service connection and try again.</p>
    {/* Native navigation retries the server render and loads current build assets.
        An empty href preserves the current path and query without using the router. */}
    <RetryButton href="" />
  </main>;
}
