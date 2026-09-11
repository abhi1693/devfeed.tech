"use client";

import Link from "next/link";
import { useState } from "react";
import { DropdownMenu } from "radix-ui";
import { Bell, ChevronDown, Hash, LayoutGrid, LogOut, Settings } from "lucide-react";
import { useUser } from "./user-account";
import { ProfileAvatar } from "./profile-avatar";

export function UserMenu() {
  const { user, profile, signOut } = useUser();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  if (!user) return null;
  const name = profile?.display_name || user.name;
  const label = name || user.email || "Your account";
  async function logout() {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await signOut();
    } catch {
      setError("Couldn’t sign out. Please try again.");
      setBusy(false);
    }
  }
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>
        <button
          className="user-menu-trigger"
          aria-label={`User menu: ${label}`}
        >
          <ProfileAvatar name={name} url={profile?.avatar_url} />
          <span className="user-menu-name">{label}</span>
          <ChevronDown
            className="user-menu-chevron"
            size={14}
            aria-hidden="true"
          />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          className="user-menu-content"
          align="end"
          sideOffset={10}
          collisionPadding={12}
        >
          <DropdownMenu.Label className="user-menu-identity">
            <span>{label}</span>
            {name && user.email && (
              <span className="user-menu-email">{user.email}</span>
            )}
          </DropdownMenu.Label>
          <DropdownMenu.Separator className="user-menu-separator" />
          <DropdownMenu.Item asChild>
            <Link href="/settings/profile">
              <Settings size={17} aria-hidden="true" />
              Profile settings
            </Link>
          </DropdownMenu.Item>
          <DropdownMenu.Item asChild>
            <Link href="/settings/notifications">
              <Bell size={17} aria-hidden="true" />
              Notifications
            </Link>
          </DropdownMenu.Item>
          <DropdownMenu.Item asChild>
            <Link href="/settings/feed">
              <LayoutGrid size={17} aria-hidden="true" />
              Feed settings
            </Link>
          </DropdownMenu.Item>
          <DropdownMenu.Item asChild>
            <Link href="/preferences">
              <Hash size={17} aria-hidden="true" />
              Your topics
            </Link>
          </DropdownMenu.Item>
          <DropdownMenu.Separator className="user-menu-separator" />
          <DropdownMenu.Item
            disabled={busy}
            onSelect={(event) => {
              event.preventDefault();
              void logout();
            }}
            aria-busy={busy}
          >
            <LogOut size={17} aria-hidden="true" />
            {busy ? "Signing out…" : "Sign out"}
          </DropdownMenu.Item>
          {error && (
            <p className="user-menu-error" role="alert">
              {error}
            </p>
          )}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}
