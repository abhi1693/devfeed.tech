"use client";

import { useState } from "react";
import { ChevronDown, LoaderCircle, LogOut, UserRound } from "lucide-react";
import { Button } from "@/components/atoms/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/atoms/dropdown-menu";
import type { AdminIdentity } from "@/lib/api/generated/models";
import { adminAuthLogout } from "@/lib/api/generated/admin";
import { returnToLogin } from "@/lib/api/client";
import { notifyFailure } from "@/lib/notifications";

export function UserMenu({ admin }: { admin: AdminIdentity }) {
  const [busy, setBusy] = useState(false);
  const name = admin.name?.trim();
  const email = admin.email?.trim();
  const label = name || email || "Admin account";
  const words = name?.split(/\s+/);
  const initials = words?.length
    ? [words[0], ...(words.length > 1 ? [words[words.length - 1]] : [])].map(word => Array.from(word)[0]).join("").toUpperCase()
    : undefined;

  async function signOut() {
    if (busy) return;
    setBusy(true);
    try {
      await adminAuthLogout({ headers: { "X-CSRF-Token": admin.csrf_token } });
      returnToLogin(true);
    } catch (error) {
      notifyFailure(error, "Could not sign out");
      setBusy(false);
    }
  }

  return <DropdownMenu>
    <DropdownMenuTrigger asChild>
      <Button variant="ghost" size="sm" className="h-9 gap-2 px-1.5 has-[>svg]:px-1.5" aria-label={`User menu: ${label}`}>
        <span aria-hidden className="flex size-7 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-semibold text-muted-foreground">
          {initials || <UserRound className="size-4" />}
        </span>
        <span className="hidden max-w-40 truncate md:inline">{label}</span>
        <ChevronDown aria-hidden className="size-3.5 text-muted-foreground" />
      </Button>
    </DropdownMenuTrigger>
    <DropdownMenuContent className="w-64">
      <DropdownMenuLabel className="space-y-1">
        <p className="break-words">{label}</p>
        {name && email && <p className="break-words text-xs font-normal text-muted-foreground">{email}</p>}
      </DropdownMenuLabel>
      <DropdownMenuSeparator />
      <DropdownMenuItem disabled={busy} aria-busy={busy} onSelect={event => { event.preventDefault(); void signOut(); }}>
        {busy ? <LoaderCircle aria-hidden className="animate-spin motion-reduce:animate-none" /> : <LogOut aria-hidden />}
        {busy ? "Signing out…" : "Sign out"}
      </DropdownMenuItem>
    </DropdownMenuContent>
  </DropdownMenu>;
}
