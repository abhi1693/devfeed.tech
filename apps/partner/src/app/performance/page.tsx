import { PortalPage, type PortalSearchParams } from "@/lib/server/portal-page";
export const dynamic = "force-dynamic";
export const metadata = { title: "Performance" };
export default function Page({ searchParams }: { searchParams: Promise<PortalSearchParams> }) {
  return <PortalPage section="performance" searchParams={searchParams} />;
}
