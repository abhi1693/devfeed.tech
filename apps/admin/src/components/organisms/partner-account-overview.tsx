"use client";

import Link from "next/link";
import { ChevronDown, Pencil } from "lucide-react";
import { Button } from "@devfeed/ui/button";
import { Badge } from "@devfeed/ui/badge";
import { ResourcePanel } from "@devfeed/ui/resource-layout";
import { audienceValue, experienceLevels, hasAudience } from "@devfeed/ui/audience-targeting";
import { adFormatLabel, placementSurfaces } from "@devfeed/ui/advertising-formats";
import type { AccountOut } from "@/lib/api/generated/models";
import { partnerAccountHref } from "@/lib/partner-account-routes";
import type { AccountSettingsTab } from "./partner-account-management";

export function PartnerAccountOverview({ account }: { account: AccountOut }) {
  const settings = partnerAccountHref({
    resource: "account",
    action: "edit",
    accountId: account.id,
  });
  const audience = audienceValue(account.audience);
  const restricted = hasAudience(audience);
  const sponsored = account.effective_promotion_kind === "ad";
  function edit(tab: AccountSettingsTab, label: string) {
    return (
      <Button asChild variant="ghost" size="sm" className="h-7 px-2 text-xs">
        <Link href={`${settings}?tab=${tab}`} prefetch={false} aria-label={label}>
          <Pencil size={12} aria-hidden="true" /> Edit
        </Link>
      </Button>
    );
  }
  return (
    <div className="space-y-4 [--resource-field-padding-block:0.5rem] [--resource-panel-gap:0.75rem] [--resource-panel-padding:1rem]">
      <div className="grid min-w-0 items-start gap-4 lg:grid-cols-2">
        <ResourcePanel
          title="Delivery defaults"
          actions={edit("delivery", "Edit delivery defaults")}
          fields={[
            {
              label: "Default promotion",
              value: (
                <div>
                  <p className="font-medium">
                    {sponsored ? "Sponsored" : "Product recommendation"}
                  </p>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    {account.promotion_kind_source === "account"
                      ? "Account override"
                      : "Inherited from tier"}
                  </p>
                </div>
              ),
            },
            {
              label: "Product acceptance",
              value:
                account.approval_mode === "trusted"
                  ? "Trusted account products"
                  : "Automatic capability checks",
            },
            {
              label: "Click destination",
              value:
                account.outbound_destination === "listing_url"
                  ? "Partner listing"
                  : "Product website",
            },
            {
              label: "Product enrollment",
              value:
                (account.auto_include_products ?? true)
                  ? "Automatic inclusion"
                  : "Individual selection",
            },
            {
              label: "Recommendations",
              value: sponsored
                ? "Sponsored promotion is active"
                : (account.organic_enabled ?? true)
                  ? "Enabled"
                  : "Disabled",
            },
          ]}
        />
        <div className="min-w-0 space-y-4">
          <ResourcePanel title="Formats & placements" actions={edit("formats", "Edit ad formats")}>
            <dl className="space-y-3 text-sm">
              <div>
                <dt className="mb-2 text-xs text-muted-foreground">Automatic sponsored formats</dt>
                <dd className="flex flex-wrap gap-1.5">
                  {(account.ad_formats ?? ["feed"]).length ? (
                    (account.ad_formats ?? ["feed"]).map((format) => (
                      <Badge key={format} variant="secondary">
                        {adFormatLabel(format)}
                      </Badge>
                    ))
                  ) : (
                    <span className="text-muted-foreground">None enabled</span>
                  )}
                </dd>
                {!sponsored && (
                  <p className="mt-2 text-xs text-muted-foreground">
                    Applied when the default promotion is Sponsored.
                  </p>
                )}
              </div>
              <div>
                <dt className="mb-2 text-xs text-muted-foreground">Eligible placement contexts</dt>
                <dd className="flex flex-wrap gap-1.5">
                  {placementSurfaces
                    .filter((surface) =>
                      (
                        account.promotion_surfaces ?? placementSurfaces.map((item) => item.value)
                      ).includes(surface.value),
                    )
                    .map((surface) => (
                      <Badge key={surface.value} variant="outline">
                        {surface.label}
                      </Badge>
                    ))}
                </dd>
              </div>
            </dl>
          </ResourcePanel>
          <ResourcePanel title="Audience" actions={edit("audience", "Edit audience targeting")}>
            {!restricted ? (
              <p className="text-sm">
                No audience restrictions
                <span className="mt-1 block text-xs text-muted-foreground">
                  Products still need an eligible placement context.
                </span>
              </p>
            ) : (
              <div className="space-y-2 text-sm">
                <p>
                  {audience.experience_levels.length
                    ? audience.experience_levels
                        .map(
                          (value) =>
                            experienceLevels.find((level) => level.value === value)?.label ?? value,
                        )
                        .join(" · ")
                    : "Any experience level"}
                </p>
                <p className="text-muted-foreground">
                  {audience.language_topic_ids.length
                    ? `${audience.language_topic_ids.length} selected programming language${audience.language_topic_ids.length === 1 ? "" : "s"}`
                    : "Any programming language"}{" "}
                  ·{" "}
                  {audience.tool_topic_ids.length
                    ? `${audience.tool_topic_ids.length} selected developer tool${audience.tool_topic_ids.length === 1 ? "" : "s"}`
                    : "Any developer tool"}
                </p>
                {!account.advanced_targeting && (
                  <p className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs">
                    Saved targeting requires Platinum or Diamond. Review these restrictions before
                    delivery.
                  </p>
                )}
              </div>
            )}
          </ResourcePanel>
        </div>
      </div>
      <details className="group rounded-lg border bg-card px-4 py-3">
        <summary className="flex cursor-pointer list-none items-center justify-between gap-3 text-sm font-medium [&::-webkit-details-marker]:hidden">
          Included partnership benefits
          <ChevronDown
            size={16}
            aria-hidden="true"
            className="transition-transform group-open:rotate-180"
          />
        </summary>
        <ul className="mt-3 grid gap-x-8 gap-y-2 text-sm text-muted-foreground sm:grid-cols-2">
          {account.benefits.map((benefit) => (
            <li key={benefit}>{benefit}</li>
          ))}
        </ul>
      </details>
      <p className="break-all text-xs text-muted-foreground">
        Account ID <code className="ml-1 select-all">{account.id}</code>
      </p>
    </div>
  );
}
