import { Overview } from "@/components/organisms/overview";
import { initialUserSettings } from "@/lib/server/session";
export const metadata = { title: "Overview" };
export default async function AdminPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const [settings, query] = await Promise.all([initialUserSettings(), searchParams]);
  const days = settings.defaults?.overview_days ?? 30;
  const from = typeof query.from === "string" ? query.from : undefined;
  const to = typeof query.to === "string" ? query.to : undefined;
  const initialRange =
    from !== undefined || to !== undefined
      ? { from, to }
      : { from: `now-${days - 1}d/d`, to: "now" };
  return <Overview initialRange={initialRange} />;
}
