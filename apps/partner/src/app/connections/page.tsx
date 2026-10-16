import { PortalPage, type PortalSearchParams } from "@/lib/server/portal-page";
export const dynamic = "force-dynamic";
export const metadata = { title: "Import connections" };
export default async function Page({
  searchParams,
}: {
  searchParams: Promise<PortalSearchParams>;
}) {
  const search = await searchParams;
  const offset = Number(
    typeof search.connection_offset === "string" ? search.connection_offset : undefined,
  );
  return (
    <PortalPage
      section="connections"
      searchParams={Promise.resolve(search)}
      connectionsRoute={{
        view: "list",
        offset: Number.isSafeInteger(offset) && offset >= 0 ? offset : 0,
        q: typeof search.q === "string" ? search.q.slice(0, 200) : "",
      }}
    />
  );
}
