"use client";

import Link from "next/link";
import { useEffect, useRef } from "react";
import { LayoutGrid, MousePointer2, Settings2, Users } from "lucide-react";
import { Badge } from "@devfeed/ui/badge";
import { ResourceHeading, ResourcePanel } from "@devfeed/ui/resource-layout";
import { accountSettingsPath, portalPath, type AccountSettingsSection } from "@/lib/routes";
import type { Account } from "@/lib/types";
import { PartnerDestination } from "./partner-destination";
import { PartnerAdFormats } from "./partner-ad-formats";
import { PartnerAudience } from "./partner-audience";

const sections = [
  {
    value: "general",
    label: "General",
    icon: Settings2,
    description: "Account details, partnership benefits, and product acceptance.",
  },
  {
    value: "destination",
    label: "Click destination",
    icon: MousePointer2,
    description: "Choose where readers go when they click a product placement.",
  },
  {
    value: "formats",
    label: "Ad formats",
    icon: LayoutGrid,
    description: "Choose native formats for this account’s automatic placements.",
  },
  {
    value: "audience",
    label: "Audience",
    icon: Users,
    description: "Limit eligible readers by experience, programming language, and tools.",
  },
] as const;

export function PartnerAccountSettings({
  account,
  section,
  csrfToken,
  onSaved,
  onUnavailable,
  query = "",
}: {
  account: Account;
  section: AccountSettingsSection;
  csrfToken: string;
  onSaved: (account: Account) => void;
  onUnavailable: () => void;
  query?: string;
}) {
  const navigation = useRef<HTMLElement>(null);
  useEffect(() => {
    function revealCurrentSection() {
      const nav = navigation.current;
      const active = nav?.querySelector<HTMLAnchorElement>('[aria-current="page"]');
      if (!nav || !active) return;
      const container = nav.getBoundingClientRect();
      const item = active.getBoundingClientRect();
      if (item.left < container.left) nav.scrollLeft += item.left - container.left;
      else if (item.right > container.right) nav.scrollLeft += item.right - container.right;
    }
    revealCurrentSection();
    window.addEventListener("resize", revealCurrentSection);
    return () => window.removeEventListener("resize", revealCurrentSection);
  }, [account.id, section]);
  const current = sections.find((item) => item.value === section)!;
  const params = new URLSearchParams(query);
  const context = params.size ? `?${params}` : "";
  const props = { account, csrfToken, onSaved, onUnavailable };
  return (
    <section className="min-w-0 space-y-4">
      <ResourceHeading
        homeHref={portalPath()}
        homeLabel="Dashboard"
        siteTitle="DevFeed Partners"
        title={`${account.name} settings`}
        browserTitle={`${current.label} · ${account.name}`}
        description={current.description}
        trail={[{ label: "Settings", href: `${portalPath("settings")}${context}` }]}
      />
      <nav
        ref={navigation}
        aria-label="Account settings sections"
        className="flex min-w-0 gap-1 overflow-x-auto border-b"
      >
        {sections.map(({ value, label, icon: Icon }) => (
          <Link
            key={value}
            href={`${accountSettingsPath(account.id, value)}${context}`}
            prefetch={false}
            aria-current={value === section ? "page" : undefined}
            className={`flex shrink-0 items-center gap-2 border-b-2 px-2 py-2.5 text-xs transition-colors hover:bg-accent hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-ring sm:px-4 sm:text-sm ${value === section ? "border-foreground font-medium text-foreground" : "border-transparent text-muted-foreground"}`}
          >
            <Icon className="size-4 shrink-0" aria-hidden />
            {label}
          </Link>
        ))}
      </nav>
      <div
        key={`${account.id}/${section}`}
        className={section === "general" ? "max-w-5xl" : "max-w-4xl"}
      >
        {section === "general" ? (
          <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
            <ResourcePanel
              title="Partner account"
              fields={[
                { label: "Name", value: account.name },
                {
                  label: "Tier",
                  value: account.tier.charAt(0).toUpperCase() + account.tier.slice(1),
                },
                {
                  label: "Status",
                  value: (
                    <Badge variant={account.status === "active" ? "success" : "warning"}>
                      {account.status}
                    </Badge>
                  ),
                },
                {
                  label: "Default promotion",
                  value: `${account.effective_promotion_kind === "ad" ? "Sponsored" : "Product recommendation"} · ${account.promotion_kind_source === "tier" ? "inherited from tier" : "account setting"}`,
                },
                {
                  label: "Product acceptance",
                  value:
                    account.approval_mode === "trusted"
                      ? "Trusted partner products accepted automatically"
                      : "Automatic capability checks",
                },
              ]}
            />
            <ResourcePanel title="Partnership benefits">
              {account.benefits.length ? (
                <ul className="list-disc space-y-2 pl-5 text-sm">
                  {account.benefits.map((benefit, index) => (
                    <li key={index}>{benefit}</li>
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-muted-foreground">
                  No additional benefits listed for this account.
                </p>
              )}
              <p className="mt-4 text-sm text-muted-foreground">
                Imported and manually added products use this account’s defaults. Placement delivery
                also requires relevant matches and available capacity.
              </p>
            </ResourcePanel>
          </div>
        ) : section === "destination" ? (
          <PartnerDestination {...props} />
        ) : section === "formats" ? (
          <PartnerAdFormats {...props} />
        ) : (
          <PartnerAudience {...props} />
        )}
      </div>
    </section>
  );
}
