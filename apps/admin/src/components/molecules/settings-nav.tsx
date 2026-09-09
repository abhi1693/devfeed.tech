"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Bell, Monitor, SlidersHorizontal, UserRound } from "lucide-react";
import { settingsSections } from "@/lib/settings";
import { PageHeading } from "./page-heading";
import { cn } from "@/lib/utils";
const icons = { profile: UserRound, notifications: Bell, appearance: Monitor, defaults: SlidersHorizontal };
export function SettingsNav() {
  const active = usePathname().split("/")[2] || "profile";
  const current = settingsSections.find(item => item.id === active);
  return <div className="min-w-0 space-y-4">
    <PageHeading title="Settings" browserTitle={`${current?.label ?? "Account"} settings`} />
    <nav aria-label="Settings sections" className="flex gap-1 overflow-x-auto border-b">
      {settingsSections.map(item => {
        const Icon = icons[item.id];
        return <Link key={item.id} href={`/settings/${item.id}`} aria-current={active === item.id ? "page" : undefined} className={cn("flex shrink-0 items-center gap-2 border-b-2 border-transparent px-2 py-2.5 text-xs text-muted-foreground hover:bg-muted/50 hover:text-foreground sm:px-4 sm:text-sm", active === item.id && "border-primary font-medium text-foreground")}><Icon className="hidden size-4 sm:block" aria-hidden />{item.label}</Link>;
      })}
    </nav>
  </div>;
}
