import { Overview } from "@/components/organisms/overview";
import { initialOverview, initialUserSettings } from "@/lib/server/session";
export const metadata = { title: "Overview" };
export default async function AdminPage() {
  return <Overview initialData={await initialOverview((await initialUserSettings()).defaults?.overview_days ?? 30)} />;
}
