"use client";
import { portalPath, type PortalSection } from "@/lib/routes";

import Image from "next/image";
import Link from "next/link";
import { LayoutDashboard, BarChart3, Shapes, Plug, Settings } from "lucide-react";
import brandMark from "@devfeed/theme/assets/devfeed-mark.png";
import { setBrowserTheme, useBrowserTheme } from "@devfeed/ui/browser-theme";
import { CollapsibleSidebar } from "@devfeed/ui/sidebar";
import { ThemeToggleButton } from "@devfeed/ui/theme-toggle";
import { UserMenu } from "@/components/user-menu";
import type { Identity } from "@/lib/types";

export function PortalShell({
  identity,
  onSignOut,
  children,
  section = "overview",
  query = "",
}: {
  identity: Identity;
  onSignOut: () => Promise<void>;
  children: React.ReactNode;
  section?: PortalSection;
  query?: string;
}) {
  const theme = useBrowserTheme();
  const links = [
    { id: "overview", label: "Dashboard", icon: LayoutDashboard },
    { id: "performance", label: "Performance", icon: BarChart3 },
    { id: "products", label: "Products", icon: Shapes },
    { id: "connections", label: "Connections", icon: Plug },
    { id: "settings", label: "Settings", icon: Settings },
  ];
  return (
    <div className="partner-workspace min-h-dvh">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:z-50 focus:bg-card focus:p-4"
      >
        Skip to content
      </a>
      <header className="border-b bg-card">
        <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-2.5 sm:px-5">
          <Link
            href={`${portalPath()}${query}`}
            aria-label="DevFeed Partners home"
            className="inline-flex items-center gap-3 rounded-sm focus-visible:outline-2"
          >
            <span className="devfeed-brand">
              <Image
                className="devfeed-brand-mark"
                src={brandMark}
                alt=""
                width={32}
                height={32}
                priority
              />
              <span>devfeed.</span>
            </span>
          </Link>
          <div className="flex items-center gap-2">
            <ThemeToggleButton theme={theme} onChange={setBrowserTheme} />
            <UserMenu identity={identity} onSignOut={onSignOut} query={query} />
          </div>
        </div>
      </header>
      <div className="lg:flex">
        <CollapsibleSidebar
          id="partner-sidebar"
          label="Partner navigation"
          storageKey="devfeed:partner-sidebar-expanded"
          variant="workspace"
        >
          <nav aria-label="Partner portal">
            <div className="sidebar-group">
              <h2 className="sidebar-section-title">Workspace</h2>
              <ul>
                {links.map(({ id, label, icon: Icon }) => (
                  <li key={id}>
                    <Link
                      href={`${portalPath(id as PortalSection)}${query}`}
                      prefetch={false}
                      aria-current={section === id ? "page" : undefined}
                      title={label}
                      aria-label={label}
                      className={`nav-item${section === id ? " active" : ""}`}
                    >
                      <Icon size={20} aria-hidden="true" />
                      <span>{label}</span>
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          </nav>
        </CollapsibleSidebar>
        <main id="main" className="min-w-0 flex-1 px-4 py-4 sm:px-5">
          <div className="w-full min-w-0">{children}</div>
        </main>
      </div>
    </div>
  );
}
