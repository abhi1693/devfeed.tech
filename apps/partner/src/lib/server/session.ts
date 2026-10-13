import "server-only";
import { cookies } from "next/headers";
import { notFound, redirect } from "next/navigation";
import { partnerApiOrigin } from "./config";
import type { Account, Identity, AccountPage } from "../types";

export async function portalSession(
  accountOffset = 0,
  accountId?: string,
  accountSearch = "",
  accountLimit = 25,
): Promise<{ identity: Identity; accounts: AccountPage; requestedAccount?: Account }> {
  const jar = await cookies();
  const session = jar.get("__Host-devfeed_partner_session") ?? jar.get("devfeed_partner_session");
  if (!session) redirect("/login");
  const headers = { Cookie: `${session.name}=${session.value}` };
  const fetchPrivate = async <T>(path: string, accountLookup = false): Promise<T> => {
    const response = await fetch(`${partnerApiOrigin()}/v1/partner/${path}`, {
      headers,
      cache: "no-store",
      redirect: "error",
      signal: AbortSignal.timeout(10_000),
    });
    if (response.status === 401 || response.status === 403) redirect("/login?error=access_denied");
    if (accountLookup && response.status === 404) notFound();
    if (!response.ok) throw new Error("Partner service unavailable. Please retry.");
    return response.json();
  };
  const identity = await fetchPrivate<Identity>("auth/me");
  const [accounts, requestedAccount] = await Promise.all([
    fetchPrivate<AccountPage>(
      `accounts?offset=${accountOffset}&limit=${accountLimit}&q=${encodeURIComponent(accountSearch)}`,
    ),
    accountId ? fetchPrivate<Account>(`accounts/${accountId}`, true) : undefined,
  ]);
  return { identity, accounts, requestedAccount };
}
