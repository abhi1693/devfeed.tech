import { Overview } from "@/components/organisms/overview";
import { initialOverview } from "@/lib/server/session";
export default async function AdminPage() {
  return <Overview initialData={await initialOverview()} />;
}
