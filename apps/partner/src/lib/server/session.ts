import "server-only";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { partnerApiOrigin } from "./config";
import type { Identity, AccountPage } from "../types";

export async function portalSession(
  accountOffset = 0,
): Promise<{ identity: Identity; accounts: AccountPage }> {
  const jar = await cookies();
  const session = jar.get("__Host-devfeed_partner_session") ?? jar.get("devfeed_partner_session");
  if (!session) redirect("/login");
  const headers = { Cookie: `${session.name}=${session.value}` };
  const fetchPrivate = async (path: string) => {
    const response = await fetch(`${partnerApiOrigin()}/v1/partner/${path}`, {
      headers,
      cache: "no-store",
      redirect: "error",
      signal: AbortSignal.timeout(10_000),
    });
    if (response.status === 401 || response.status === 403) redirect("/login?error=access_denied");
    if (!response.ok) throw new Error("Partner service unavailable. Please retry.");
    return response.json();
  };
  const identity = (await fetchPrivate("auth/me")) as Identity;
  const accounts = (await fetchPrivate(`accounts?offset=${accountOffset}`)) as AccountPage;
  return { identity, accounts };
}
