"use client";

import { RefreshCw } from "lucide-react";

type Props = {
  label?: string;
  size?: "default" | "sm";
} & (
  | { href: string; onRetry?: never; pending?: never }
  | {
      href?: never;
      onRetry: () => void;
      pending?: boolean;
    }
);

export function RetryButton({
  label = "Try again",
  size = "default",
  href,
  onRetry,
  pending = false,
}: Props) {
  const content = (
    <>
      <RefreshCw size={16} aria-hidden="true" />
      {pending ? "Trying again…" : label}
    </>
  );
  if (href !== undefined)
    return (
      <a className="shared-retry-button" data-slot="button" data-size={size} href={href}>
        {content}
      </a>
    );
  return (
    <button
      type="button"
      className="shared-retry-button"
      data-slot="button"
      data-size={size}
      disabled={pending}
      aria-busy={pending}
      onClick={onRetry}
    >
      {content}
    </button>
  );
}
