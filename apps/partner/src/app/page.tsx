import { portalSession } from "@/lib/server/session";
import { Portal } from "./portal";

export const dynamic = "force-dynamic";
export default async function Page() {
  const { identity, accounts } = await portalSession();
  return <Portal identity={identity} initialAccounts={accounts} />;
}
