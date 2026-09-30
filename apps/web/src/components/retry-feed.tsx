"use client";

import { useTransition } from "react";
import { useReaderRouter } from "@/lib/reader-navigation";
import { RetryButton } from "@devfeed/ui/retry-button";

export function RetryFeed() {
  const router = useReaderRouter();
  const [pending, startTransition] = useTransition();
  return <RetryButton pending={pending} onRetry={() => startTransition(() => router.refresh())} />;
}
