"use client";

import { useTransition } from "react";
import { useRouter } from "next/navigation";
import { RefreshCw } from "lucide-react";

export function RetryFeed() {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  return (
    <button
      className="button primary"
      disabled={pending}
      onClick={() => startTransition(() => router.refresh())}
    >
      {pending ? "Trying again…" : "Try again"}
      <RefreshCw size={16} />
    </button>
  );
}
