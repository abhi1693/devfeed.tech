"use client";

import Image from "next/image";
import Link from "next/link";
import { useState } from "react";
import { LayoutDashboard, BarChart3, Shapes, Users, Menu, X, LogOut } from "lucide-react";
import brandMark from "@devfeed/theme/assets/devfeed-mark.png";
import { Button } from "@/components/atoms/button";
import { Badge } from "@/components/atoms/badge";
import type { Identity } from "@/lib/types";

export function PortalShell({
  identity,
  onSignOut,
  children,
}: {
  identity: Identity;
  onSignOut: () => Promise<void>;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState("overview");
  const [signingOut, setSigningOut] = useState(false);
  const superuser = identity.roles.includes("superuser");
  const links = [
    { id: "overview", label: "Overview", icon: LayoutDashboard },
    { id: "performance", label: "Performance", icon: BarChart3 },
    { id: "assets", label: "Products & ads", icon: Shapes },
    ...(superuser ? [{ id: "management", label: "Manage partnerships", icon: Users }] : []),
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
            href="/"
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
            <Badge variant="outline">Partners</Badge>
          </Link>
          <div className="ml-auto flex min-w-0 flex-wrap items-center gap-3">
            <div className="flex items-center gap-2 text-sm">
              <span
                className="flex size-8 items-center justify-center rounded-full bg-accent font-medium text-primary"
                aria-hidden="true"
              >
                {(identity.name ?? "Partner").slice(0, 1).toUpperCase()}
              </span>
              <span className="max-w-40 truncate">{identity.name ?? "Partner"}</span>
              {superuser && <Badge variant="secondary">Superuser</Badge>}
            </div>
            <Button
              variant="outline"
              size="sm"
              loading={signingOut}
              onClick={async () => {
                setSigningOut(true);
                try {
                  await onSignOut();
                } finally {
                  setSigningOut(false);
                }
              }}
            >
              <LogOut aria-hidden="true" />
              Sign out
            </Button>
          </div>
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
                  <a
                    href={`#${id}`}
                    aria-current={active === id ? "location" : undefined}
                    className={`flex items-center gap-2.5 rounded-md px-3 py-2 text-sm ${active === id ? "bg-accent font-medium text-foreground" : "text-muted-foreground hover:bg-accent hover:text-foreground"}`}
                    onClick={() => {
                      setActive(id);
                      setOpen(false);
                    }}
                  >
                    <Icon size={16} aria-hidden="true" />
                    {label}
                  </a>
                </li>
              ))}
            </ul>
          </nav>
          <div className="mt-6 border-t px-3 pt-4 text-xs text-muted-foreground">
            DevFeed Partners
          </div>
        </aside>
        <main id="main" className="min-w-0 flex-1 px-4 py-6 sm:px-6">
          <div className="w-full min-w-0">{children}</div>
        </main>
      </div>
    </div>
  );
}
