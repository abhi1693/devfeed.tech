"use client";
import { portalPath, type PortalSection } from "@/lib/routes";

import Image from "next/image";
import Link from "next/link";
import { useState } from "react";
import { LayoutDashboard, BarChart3, Shapes, Menu, X } from "lucide-react";
import brandMark from "@devfeed/theme/assets/devfeed-mark.png";
import { Button } from "@/components/atoms/button";
import { UserMenu } from "@/components/user-menu";
import type { Identity } from "@/lib/types";

export function PortalShell({
  identity,
  onSignOut,
  children,
  section = "overview",
  query = "",
  accountId = "",
}: {
  identity: Identity;
  onSignOut: () => Promise<void>;
  children: React.ReactNode;
  section?: "overview" | "performance" | "assets";
  query?: string;
  accountId?: string;
}) {
  const [open, setOpen] = useState(false);
  const links = [
    { id: "overview", href: "/", label: "Overview", icon: LayoutDashboard },
    { id: "performance", href: "/performance", label: "Performance", icon: BarChart3 },
    { id: "assets", href: "/assets", label: "Products & ads", icon: Shapes },
  ];
  return (
    <div className="min-h-dvh">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:z-50 focus:bg-card focus:p-4"
      >
        Skip to content
      </a>
      <header className="border-b bg-card">
        <div className="flex flex-wrap items-center justify-between gap-4 px-4 py-4 sm:px-6">
          <Link
            href={`${portalPath(accountId)}${query}`}
            aria-label="DevFeed Partners home"
            className="inline-flex items-center gap-3 rounded-sm focus-visible:outline-2"
          >
            <span className="devfeed-brand">
              <Image
                className="devfeed-brand-mark"
                src={brandMark}
                alt=""
                width={40}
                height={40}
                priority
              />
              <span>devfeed.</span>
            </span>
          </Link>
          <UserMenu identity={identity} onSignOut={onSignOut} query={query} accountId={accountId} />
        </div>
      </header>
      <div className="lg:flex">
        <div className="border-b bg-card px-4 py-2 lg:hidden">
          <Button
            variant="outline"
            size="sm"
            aria-expanded={open}
            aria-controls="partner-sidebar"
            onClick={() => setOpen(!open)}
          >
            {open ? <X /> : <Menu />} Navigation
          </Button>
        </div>
        <aside
          id="partner-sidebar"
          className={`${open ? "block" : "hidden"} shrink-0 border-b bg-card p-4 lg:sticky lg:top-0 lg:block lg:h-dvh lg:w-60 lg:overflow-y-auto lg:border-r lg:border-b-0`}
        >
          <nav aria-label="Partner portal">
            <h2 className="mb-2 px-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Workspace
            </h2>
            <ul className="space-y-0.5">
              {links.map(({ id, label, icon: Icon }) => (
                <li key={id}>
                  <Link
                    href={`${portalPath(accountId, id as PortalSection)}${query}`}
                    prefetch={false}
                    aria-current={section === id ? "page" : undefined}
                    className={`flex items-center gap-2.5 rounded-md px-3 py-2 text-sm ${section === id ? "bg-accent font-medium text-foreground" : "text-muted-foreground hover:bg-accent hover:text-foreground"}`}
                    onClick={() => {
                      setOpen(false);
                    }}
                  >
                    <Icon size={16} aria-hidden="true" />
                    {label}
                  </Link>
                </li>
              ))}
            </ul>
          </nav>
        </aside>
        <main id="main" className="min-w-0 flex-1 px-4 py-6 sm:px-6">
          <div className="w-full min-w-0">{children}</div>
        </main>
      </div>
    </div>
  );
}
