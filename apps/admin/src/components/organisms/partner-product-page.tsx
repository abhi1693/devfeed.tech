"use client";

import Link from "next/link";
import { useCallback, useState } from "react";
import { Button } from "@devfeed/ui/button";
import { useAdmin } from "@/components/molecules/admin-session";
import { DateTime } from "@/components/molecules/date-time";
import { ResourcePanel, DataValue } from "@devfeed/ui/resource-layout";
import { ResourceHeading } from "@devfeed/ui/resource-layout";
import { RequestState } from "@/components/molecules/request-state";
import { StatusBadge } from "@/components/molecules/status-badge";
import { adminPartnerToolsList, adminPartnerProductAction } from "@/lib/api/generated/admin";
import { partnerProductHref } from "@/lib/partner-runs";
import { useRefreshInterval } from "@/lib/use-refresh-interval";
import { useRequest } from "@/lib/use-request";
import { importConnectionHref, partnershipTrail } from "./partner-connections";
import { RelatedPartnerRuns } from "./partner-runs";

export function PartnerProductPage({
  id,
  section = "details",
}: {
  id: string;
  section?: "details" | "related";
}) {
  const admin = useAdmin();
  const interval = useRefreshInterval();
  const [revision, setRevision] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error>();
  const load = useCallback(
    async (signal: AbortSignal) => {
      const page = await adminPartnerToolsList({ product_id: id, limit: 1 }, { signal });
      if (!page.items[0]) throw new Error("Product not found.");
      return page.items[0];
    },
    [id],
  );
  const result = useRequest(`partner-product/${id}/${revision}`, load, interval * 1000);
  const product = result.data;
  async function act(action: "exclude" | "retry") {
    if (!product) return;
    setBusy(true);
    setError(undefined);
    try {
      await adminPartnerProductAction(
        product.id,
        { action, expected_revision: product.revision },
        { headers: { "X-CSRF-Token": admin.csrf_token } },
      );
      setRevision((n) => n + 1);
    } catch (error) {
      setError(error instanceof Error ? error : new Error("Could not update product."));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="space-y-6">
      <ResourceHeading
        title={product?.name ?? "Product"}
        trail={[...partnershipTrail, { label: "Products", href: "/partnerships/products" }]}
      >
        {product && (
          <>
            <Button
              variant="outline"
              size="sm"
              disabled={
                busy ||
                result.loading ||
                product.status === "withdrawn" ||
                !(
                  product.listings.some(
                    (listing) =>
                      listing.active &&
                      listing.connection_enabled &&
                      listing.identity_status === "resolved",
                  ) || product.manual_sources?.some((source) => source.active)
                )
              }
              onClick={() => void act("retry")}
            >
              {product.excluded ? "Include and recheck" : "Retry checks"}
            </Button>
            {!product.excluded && (
              <Button
                variant="outline"
                size="sm"
                disabled={busy || result.loading}
                onClick={() => void act("exclude")}
              >
                Exclude product
              </Button>
            )}
          </>
        )}
      </ResourceHeading>
      <RequestState
        loading={result.loading}
        error={error ?? result.error}
        retry={() => setRevision((n) => n + 1)}
      />
      {product && (
        <>
          <nav aria-label="Object sections" className="flex gap-5 overflow-x-auto border-b">
            {(["details", "related"] as const).map((tab) => (
              <Link
                key={tab}
                prefetch={false}
                href={partnerProductHref(id) + (tab === "details" ? "" : "/related")}
                aria-current={section === tab ? "page" : undefined}
                className={`whitespace-nowrap border-b-2 px-1 pb-3 text-sm ${section === tab ? "border-primary font-medium" : "border-transparent text-muted-foreground hover:text-foreground"}`}
              >
                {tab === "details" ? "Details" : "Related objects"}
              </Link>
            ))}
          </nav>
          {section === "related" ? (
            <div className="space-y-8">
              {!!product.manual_sources?.length && (
                <section className="space-y-3" aria-label="Direct accounts">
                  <h2 className="font-semibold">
                    Direct accounts ({product.manual_sources.length})
                  </h2>
                  <div className="grid items-start gap-6 xl:grid-cols-2">
                    {product.manual_sources.map((source) => (
                      <ResourcePanel
                        key={source.account_id}
                        title={source.account_name}
                        fields={[
                          {
                            label: "Partner account",
                            value: (
                              <Link
                                href={`/partnerships/accounts/${source.account_id}`}
                                prefetch={false}
                                className="text-primary hover:underline"
                              >
                                {source.account_name}
                              </Link>
                            ),
                          },
                          { label: "Source", value: "Added directly" },
                          {
                            label: "Status",
                            value: <StatusBadge value={source.active ? "Active" : "Archived"} />,
                          },
                          {
                            label: "Account acceptance",
                            value: (
                              <StatusBadge
                                value={
                                  source.commercial_approved
                                    ? source.approval_source === "trusted"
                                      ? "Accepted · trusted partner"
                                      : "Accepted · verified"
                                    : "Awaiting acceptance"
                                }
                              />
                            ),
                          },
                          { label: "Updated", value: <DateTime value={source.updated_at} /> },
                        ]}
                      />
                    ))}
                  </div>
                </section>
              )}
              {!!product.listings.length && (
                <section className="space-y-3" aria-label="Platform listings">
                  <h2 className="font-semibold">Platform listings ({product.listings.length})</h2>
                  <div className="grid items-start gap-6 xl:grid-cols-2">
                    {product.listings.map((listing) => (
                      <ResourcePanel
                        key={listing.id}
                        title={listing.platform_name}
                        fields={[
                          {
                            label: "Import connection",
                            value: (
                              <Link
                                prefetch={false}
                                className="text-primary hover:underline"
                                href={importConnectionHref(listing.provider)}
                              >
                                {listing.platform_name}
                              </Link>
                            ),
                          },
                          { label: "Name", value: listing.name },
                          {
                            label: "Partner account",
                            value: listing.account_id ? (
                              <Link
                                href={`/partnerships/accounts/${listing.account_id}`}
                                prefetch={false}
                                className="text-primary hover:underline"
                              >
                                {listing.account_name ?? "View partner account"}
                              </Link>
                            ) : (
                              "Unassigned"
                            ),
                          },
                          {
                            label: "Account acceptance",
                            value: (
                              <StatusBadge
                                value={
                                  listing.commercial_approved
                                    ? listing.approval_source === "trusted"
                                      ? "Accepted · trusted partner"
                                      : "Accepted · verified"
                                    : "Awaiting acceptance"
                                }
                              />
                            ),
                          },
                          {
                            label: "Platform product ID",
                            value: <DataValue value={listing.external_id} />,
                          },
                          {
                            label: "Status",
                            value: (
                              <StatusBadge
                                value={
                                  !listing.active
                                    ? "Unavailable"
                                    : !listing.connection_enabled
                                      ? "Paused"
                                      : "Active"
                                }
                              />
                            ),
                          },
                          {
                            label: "Identity",
                            value: <StatusBadge value={listing.identity_status} />,
                          },
                          {
                            label: "Identity details",
                            value: <DataValue value={listing.identity_reason} />,
                          },
                          {
                            label: "Description",
                            value: <DataValue value={listing.description} />,
                          },
                          { label: "Pricing", value: <DataValue value={listing.pricing} /> },
                          {
                            label: "Attribution",
                            value: <DataValue value={listing.attribution} />,
                          },
                          {
                            label: "Display information source",
                            value: <DataValue value={product.metadata_listing_id === listing.id} />,
                          },
                          { label: "Website", value: <DataValue value={listing.product_url} /> },
                          {
                            label: "Listing",
                            value: (
                              <a
                                className="text-primary hover:underline"
                                href={listing.listing_url}
                                target="_blank"
                                rel="noopener noreferrer"
                              >
                                View {listing.platform_name} listing
                              </a>
                            ),
                          },
                          { label: "Updated", value: <DateTime value={listing.updated_at} /> },
                        ]}
                      />
                    ))}
                  </div>
                </section>
              )}
              <RelatedPartnerRuns kind="pipeline" filters={{ product_id: id }} />
              <RelatedPartnerRuns kind="evaluations" filters={{ product_id: id }} />
            </div>
          ) : (
            <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
              <div className="space-y-6">
                <ResourcePanel
                  title="Product"
                  fields={[
                    { label: "Name", value: product.name },
                    { label: "Website", value: <DataValue value={product.product_url} /> },
                    { label: "Description", value: <DataValue value={product.description} /> },
                    { label: "Pricing", value: <DataValue value={product.pricing} /> },
                    { label: "Technologies", value: <DataValue value={product.technologies} /> },
                  ]}
                />
                <ResourcePanel title="Capability evidence">
                  {product.evidence?.length ? (
                    <div className="space-y-4 py-3">
                      {product.evidence.map((evidence, index) => (
                        <blockquote key={index} className="border-l-2 pl-3 text-sm">
                          <p>{evidence.quote}</p>
                          <DataValue value={evidence.url} />
                        </blockquote>
                      ))}
                    </div>
                  ) : (
                    <p className="py-3 text-sm text-muted-foreground">No verified evidence yet.</p>
                  )}
                </ResourcePanel>
                <ResourcePanel title="Promotion policy">
                  <div className="space-y-3 py-3 text-sm">
                    <p className="font-medium">
                      {product.commercial_approved
                        ? "Accepted for associated partner accounts"
                        : "Awaiting account acceptance"}
                      {product.approval_sources?.includes("trusted") &&
                        " · trusted account products"}
                    </p>
                    <p className="text-muted-foreground">
                      This product inherits promotion settings from each account that added it
                      directly or imported it through a connection. Account and tier defaults
                      control product or sponsored appearances; trusted account products can be
                      accepted automatically while capability checks continue. Contextual matches
                      are preferred; sponsored feed placements can fill unmatched feeds. Article and
                      topic placements require a relevant match. Exclusions still apply.
                    </p>
                    <Link
                      href="/partnerships/accounts"
                      prefetch={false}
                      className="text-primary hover:underline"
                    >
                      Manage partner account defaults and campaigns
                    </Link>
                  </div>
                </ResourcePanel>
              </div>
              <div className="space-y-6">
                <ResourcePanel
                  title="Assessment"
                  fields={[
                    {
                      label: "Status",
                      value: (
                        <StatusBadge
                          value={product.excluded ? "Excluded" : product.assessment.state}
                        />
                      ),
                    },
                    { label: "Reason", value: <DataValue value={product.assessment.reason} /> },
                    {
                      label: "Eligible for matching",
                      value: <DataValue value={product.eligible} />,
                    },
                    {
                      label: "Verified",
                      value: product.verified_at ? (
                        <DateTime value={product.verified_at} />
                      ) : (
                        "Not verified yet"
                      ),
                    },
                  ]}
                />
                <ResourcePanel
                  title="Record information"
                  fields={[
                    { label: "ID", value: <DataValue value={product.id} /> },
                    { label: "Revision", value: <DataValue value={product.revision} /> },
                    { label: "Updated", value: <DateTime value={product.updated_at} /> },
                    {
                      label: "Sources",
                      value: (
                        <Link
                          prefetch={false}
                          href={`${partnerProductHref(id)}/related`}
                          className="text-primary hover:underline"
                        >
                          {[
                            ...new Set(product.listings.map((listing) => listing.platform_name)),
                            ...(product.manual_sources ?? []).map(
                              (source) => `Direct · ${source.account_name}`,
                            ),
                          ].join(", ")}
                        </Link>
                      ),
                    },
                  ]}
                />
              </div>
            </div>
          )}
        </>
      )}
    </section>
  );
}
