"use client";

import { Button } from "@/components/atoms/button";
import { useEffect, useRef } from "react";
import { notify } from "@/lib/notifications";

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
    <h1 className="text-xl font-semibold">Admin service unavailable</h1>
    <p className="text-sm text-muted-foreground">Could not load this page. Check the service connection and try again.</p>
    {/* Native navigation retries the server render and loads current build assets.
        An empty href preserves the current path and query without using the router. */}
    <Button variant="outline" asChild><a href="">Try again</a></Button>
  </main>;
}
