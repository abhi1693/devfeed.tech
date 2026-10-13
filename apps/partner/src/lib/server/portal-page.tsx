import "server-only";
import { notFound } from "next/navigation";
import { portalSession } from "./session";
import { Portal } from "@/app/portal";
import type { PortalSection, ProductRoute } from "@/lib/routes";
import type { ConnectionsRoute } from "@/app/partner-connections";
import type { AssetSort, SortOrder } from "@/lib/types";

type SearchValue = string | string[];
export type PortalSearchParams = {
  days?: SearchValue;
  account_id?: SearchValue;
  account_offset?: SearchValue;
  asset_offset?: SearchValue;
  sort?: SearchValue;
  order?: SearchValue;
  q?: SearchValue;
  product_offset?: SearchValue;
  include_archived?: SearchValue;
  policy_offset?: SearchValue;
  connection_offset?: SearchValue;
};
function single(value: SearchValue | undefined) {
  return typeof value === "string" ? value : undefined;
}
function offset(value: SearchValue | undefined) {
  const number = Number(single(value));
  return Number.isSafeInteger(number) && number >= 0 ? number : 0;
}

export async function PortalPage({
  section,
  searchParams,
  accountId,
  productRoute,
  connectionsRoute,
}: {
  section: PortalSection;
  productRoute?: ProductRoute;
  connectionsRoute?: ConnectionsRoute;
  accountId?: string;
  searchParams: Promise<PortalSearchParams>;
}) {
  const params = await searchParams;
  const uuidPattern = /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i;
  const accountFilter = section === "connections" ? undefined : single(params.account_id);
  if (section !== "connections" && params.account_id !== undefined && !accountFilter) notFound();
  if ([accountId, accountFilter, productRoute?.productId].some((id) => id && !uuidPattern.test(id)))
    notFound();
  if (accountId && accountFilter && accountId.toLowerCase() !== accountFilter.toLowerCase())
    notFound();
  if ("account" in params) notFound();
  const requestedAccountId = (accountId ?? accountFilter)?.toLowerCase();
  const canonicalProductRoute = productRoute?.productId
    ? { ...productRoute, productId: productRoute.productId.toLowerCase() }
    : productRoute;
  const connectionId = connectionsRoute?.connectionId?.toLowerCase();
  if (
    connectionId &&
    (connectionId.length > 200 ||
      connectionId === "new" ||
      !/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(connectionId))
  )
    notFound();
  const canonicalConnectionsRoute =
    connectionsRoute && connectionId ? { ...connectionsRoute, connectionId } : connectionsRoute;
  const days = [7, 30, 90, 365].includes(Number(single(params.days)))
    ? Number(single(params.days))
    : 30;
  const sort: AssetSort = ["name", "account", "impressions", "clicks", "ctr"].includes(
    single(params.sort) ?? "",
  )
    ? (single(params.sort) as AssetSort)
    : "name";
  const order: SortOrder = single(params.order) === "desc" ? "desc" : "asc";
  const assetOffset = offset(params.asset_offset);
  const productSearch = section === "products" ? (single(params.q) ?? "").slice(0, 200) : "";
  const accountSearch = section === "settings" ? (single(params.q) ?? "").slice(0, 200) : "";
  const productOffset = offset(params.product_offset);
  const includeArchived = section === "products" && single(params.include_archived) === "true";
  const policyOffset = offset(params.policy_offset);
  let accountOffset = section === "settings" ? offset(params.account_offset) : 0;
  let { identity, accounts, requestedAccount } = await portalSession(
    accountOffset,
    requestedAccountId,
    accountSearch,
    25,
  );
  if (accountOffset > 0 && accountOffset >= accounts.total) {
    accountOffset = 0;
    ({ identity, accounts, requestedAccount } = await portalSession(
      0,
      requestedAccountId,
      accountSearch,
      25,
    ));
  }
  return (
    <Portal
      key={`${section}:${requestedAccount?.id ?? "all"}:${days}:${sort}:${order}:${assetOffset}:${productSearch}:${productOffset}:${includeArchived}:${policyOffset}:${accountSearch}:${accountOffset}:${canonicalProductRoute?.view}:${canonicalProductRoute?.productId}:${canonicalConnectionsRoute?.view}:${canonicalConnectionsRoute?.connectionId}:${canonicalConnectionsRoute?.offset}:${canonicalConnectionsRoute?.q}`}
      identity={identity}
      initialAccounts={accounts}
      section={section}
      initialAccount={requestedAccount}
      initialDays={days}
      initialAccountOffset={accountOffset}
      initialAssetOffset={assetOffset}
      initialSort={sort}
      initialOrder={order}
      productRoute={canonicalProductRoute}
      connectionsRoute={canonicalConnectionsRoute}
      initialProductSearch={productSearch}
      initialProductOffset={productOffset}
      initialIncludeArchived={includeArchived}
      initialPolicyOffset={policyOffset}
      initialAccountSearch={accountSearch}
    />
  );
}
