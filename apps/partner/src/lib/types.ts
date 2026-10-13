import type { AccountOut } from "./api/generated/models";

export type {
  AccountOut as Account,
  AccountsOut as AccountPage,
  AssetMetrics as Asset,
  Dashboard,
  PartnerIdentity as Identity,
  Totals,
} from "./api/generated/models";

export type OutboundDestination = NonNullable<AccountOut["outbound_destination"]>;
export type AssetSort = "name" | "account" | "impressions" | "clicks" | "ctr";
export type SortOrder = "asc" | "desc";
