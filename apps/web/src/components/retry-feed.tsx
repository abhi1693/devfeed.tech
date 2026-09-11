"use client";

import { useTransition } from "react";
import { useRouter } from "next/navigation";
import { RetryButton } from "@devfeed/ui/retry-button";

export function RetryFeed() {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  return <RetryButton pending={pending} onRetry={() => startTransition(() => router.refresh())} />;
}
