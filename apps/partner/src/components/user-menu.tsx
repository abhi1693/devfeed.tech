"use client";
import { portalPath, type PortalSection } from "@/lib/routes";

import Link from "next/link";
import { useState } from "react";
import {
  ChevronDown,
  UserRound,
  LayoutDashboard,
  BarChart3,
  Shapes,
  LogOut,
  LoaderCircle,
} from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
} from "@devfeed/ui/dropdown-menu";
import { Button } from "@/components/atoms/button";
import type { Identity } from "@/lib/types";

export function UserMenu({
  identity,
  onSignOut,
  query,
  accountId = "",
}: {
  identity: Identity;
  onSignOut: () => Promise<void>;
  query: string;
  accountId?: string;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const name = identity.name?.trim();
  const email = identity.email?.trim();
  const label = name || email || "Partner account";
  const words = name?.split(/\s+/).filter(Boolean) ?? [];
  const initials = [words[0], ...(words.length > 1 ? [words.at(-1)] : [])]
    .filter(Boolean)
    .map((word) => Array.from(word!)[0])
    .join("")
    .toUpperCase();
  async function signOut() {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await onSignOut();
    } catch {
      setError("Could not sign out. Please try again.");
      setBusy(false);
    }
  }
  return (
    <DropdownMenu modal={false}>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="sm"
          className="ml-auto h-9 gap-2 px-1.5 has-[>svg]:px-1.5"
          aria-label={`User menu: ${label}`}
        >
          <span
            aria-hidden
            className="flex size-8 shrink-0 items-center justify-center rounded-full bg-accent text-xs font-semibold text-primary"
          >
            {initials || <UserRound className="size-4" />}
          </span>
          <span className="hidden max-w-40 truncate md:inline">{label}</span>
          <ChevronDown aria-hidden className="size-3.5 text-muted-foreground" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent className="w-64" sideOffset={10}>
        <DropdownMenuLabel className="space-y-1">
          <p className="break-words">{label}</p>
          {name && email && (
            <p className="break-words text-xs font-normal text-muted-foreground">{email}</p>
          )}
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        {[
          { href: "/", label: "Overview", icon: LayoutDashboard },
          { href: "/performance", label: "Performance", icon: BarChart3 },
          { href: "/assets", label: "Products & ads", icon: Shapes },
        ].map(({ href, label: entry, icon: Icon }) => (
          <DropdownMenuItem key={href} asChild>
            <Link
              href={`${portalPath(accountId, (href === "/" ? "overview" : href.slice(1)) as PortalSection)}${query}`}
              prefetch={false}
            >
              <Icon aria-hidden />
              {entry}
            </Link>
          </DropdownMenuItem>
        ))}
        <DropdownMenuSeparator />
        <DropdownMenuItem
          disabled={busy}
          aria-busy={busy}
          onSelect={(event) => {
            event.preventDefault();
            void signOut();
          }}
        >
          {busy ? (
            <LoaderCircle aria-hidden className="animate-spin motion-reduce:animate-none" />
          ) : (
            <LogOut aria-hidden />
          )}
          {busy ? "Signing out…" : "Sign out"}
        </DropdownMenuItem>
        {error && (
          <p role="alert" className="px-3 py-2 text-sm text-destructive">
            {error}
          </p>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
