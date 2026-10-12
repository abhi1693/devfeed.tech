import "server-only";
import { notFound, redirect } from "next/navigation";
import { portalPath } from "@/lib/routes";
import { portalSession } from "./session";
import { Portal } from "@/app/portal";
import type { AssetSort, SortOrder } from "@/lib/types";

export type PortalSearchParams = {
  days?: string;
  account_offset?: string;
  asset_offset?: string;
  sort?: string;
  order?: string;
};
export async function PortalPage({
  section,
  searchParams,
  accountId,
}: {
  section: "overview" | "performance" | "assets";
  accountId?: string;
  searchParams: Promise<PortalSearchParams>;
}) {
  // Account IDs are UUIDs; unknown public paths must return 404 before authentication.
  if (accountId && !/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i.test(accountId)) notFound();
  const params = await searchParams;
  if ("account" in params) notFound();
  const days = [7, 30, 90, 365].includes(Number(params.days)) ? Number(params.days) : 30;
  const sort: AssetSort = ["name", "impressions", "clicks", "ctr"].includes(params.sort ?? "")
    ? (params.sort as AssetSort)
    : "name";
  const order: SortOrder = params.order === "desc" ? "desc" : "asc";
  const requestedAssetOffset = Number(params.asset_offset);
  const assetOffset =
    Number.isSafeInteger(requestedAssetOffset) && requestedAssetOffset >= 0
      ? requestedAssetOffset
      : 0;
  const requestedOffset = Number(params.account_offset);
  let offset = Number.isSafeInteger(requestedOffset) && requestedOffset >= 0 ? requestedOffset : 0;
  let { identity, accounts, requestedAccount } = await portalSession(offset, accountId);
  if (accounts.total === 1 && offset > 0) {
    offset = 0;
    ({ identity, accounts, requestedAccount } = await portalSession(offset, accountId));
  }
  const selected = requestedAccount?.id;
  if (!accountId && accounts.items.length) {
    const scope = new URLSearchParams();
    scope.set("days", String(days));
    scope.set("sort", sort);
    scope.set("order", order);
    if (assetOffset) scope.set("asset_offset", String(assetOffset));
    if (offset) scope.set("account_offset", String(offset));
    redirect(`${portalPath(selected ?? accounts.items[0]!.id, section)}?${scope}`);
  }
  return (
    <Portal
      key={`${section}:${selected}:${days}:${offset}:${sort}:${order}:${assetOffset}`}
      identity={identity}
      initialAccounts={accounts}
      section={section}
      initialAccount={requestedAccount}
      initialDays={days}
      initialAccountOffset={offset}
      initialAssetOffset={assetOffset}
      initialSort={sort}
      initialOrder={order}
    />
  );
}
