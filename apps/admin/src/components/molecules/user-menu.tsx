"use client";

import Link from "next/link";
import { useSettings } from "@/lib/use-settings";
import { ImagePreview } from "./image-preview";
import { useState } from "react";
import { ChevronDown, LoaderCircle, LogOut, Settings, UserRound } from "lucide-react";
import { Button } from "@/components/atoms/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/atoms/dropdown-menu";
import type { AdminIdentity } from "@/lib/api/generated/models";
import { adminAuthLogout } from "@/lib/api/generated/admin";
import { returnToLogin } from "@/lib/api/client";
import { notifyFailure } from "@/lib/notifications";

export function UserMenu({ admin }: { admin: AdminIdentity }) {
  const [busy, setBusy] = useState(false);
  const { settings } = useSettings();
  const name = settings.profile.display_name?.trim() || admin.name?.trim();
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
          {settings.profile.avatar_url ? <ImagePreview key={settings.profile.avatar_url} src={settings.profile.avatar_url} compact /> : initials || <UserRound className="size-4" />}
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
      <DropdownMenuItem asChild><Link href="/settings"><Settings aria-hidden />Settings</Link></DropdownMenuItem>
      <DropdownMenuItem disabled={busy} aria-busy={busy} onSelect={event => { event.preventDefault(); void signOut(); }}>
        {busy ? <LoaderCircle aria-hidden className="animate-spin motion-reduce:animate-none" /> : <LogOut aria-hidden />}
        {busy ? "Signing out…" : "Sign out"}
      </DropdownMenuItem>
    </DropdownMenuContent>
  </DropdownMenu>;
}
