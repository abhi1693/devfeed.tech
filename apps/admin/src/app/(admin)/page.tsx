import { Overview } from "@/components/organisms/overview";
import { initialUserSettings } from "@/lib/server/session";
export const metadata = { title: "Overview" };
export default async function AdminPage() {
  const settings = await initialUserSettings();
  return <Overview initialDays={settings.defaults?.overview_days ?? 30} />;
}
