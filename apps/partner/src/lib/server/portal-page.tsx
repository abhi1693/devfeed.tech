import "server-only";
import { portalSession } from "./session";
import { Portal } from "@/app/portal";

export type PortalSearchParams = { account?: string; days?: string; account_offset?: string };
export async function PortalPage({
  section,
  searchParams,
}: {
  section: "overview" | "performance" | "assets";
  searchParams: Promise<PortalSearchParams>;
}) {
  const params = await searchParams;
  const days = [7, 30, 90, 365].includes(Number(params.days)) ? Number(params.days) : 30;
  const requestedOffset = Number(params.account_offset);
  let offset = Number.isSafeInteger(requestedOffset) && requestedOffset >= 0 ? requestedOffset : 0;
  let { identity, accounts } = await portalSession(offset);
  if (accounts.total === 1 && offset > 0) {
    offset = 0;
    ({ identity, accounts } = await portalSession(offset));
  }
  const selected = accounts.items.some((account) => account.id === params.account)
    ? (params.account ?? "")
    : "";
  return (
    <Portal
      key={`${section}:${selected}:${days}:${offset}`}
      identity={identity}
      initialAccounts={accounts}
      section={section}
      initialSelected={selected}
      initialDays={days}
      initialAccountOffset={offset}
    />
  );
}
