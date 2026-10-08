import "server-only";
import { notFound, redirect } from "next/navigation";
import { portalPath } from "@/lib/routes";
import { portalSession } from "./session";
import { Portal } from "@/app/portal";

export type PortalSearchParams = { days?: string; account_offset?: string };
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
  const requestedOffset = Number(params.account_offset);
  let offset = Number.isSafeInteger(requestedOffset) && requestedOffset >= 0 ? requestedOffset : 0;
  let { identity, accounts } = await portalSession(offset);
  if (accounts.total === 1 && offset > 0) {
    offset = 0;
    ({ identity, accounts } = await portalSession(offset));
  }
  const selected = accounts.items.find((account) => account.id === accountId)?.id;
  if (accountId && !selected) notFound();
  if (!accountId && accounts.items.length) {
    const scope = new URLSearchParams();
    scope.set("days", String(days));
    if (offset) scope.set("account_offset", String(offset));
    redirect(`${portalPath(selected ?? accounts.items[0]!.id, section)}?${scope}`);
  }
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
