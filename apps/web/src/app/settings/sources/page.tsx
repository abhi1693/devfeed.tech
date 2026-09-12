import { UserShell } from "@/components/user-shell";
import { SourcePreferences } from "@/components/source-preferences";
import { getSources } from "@/lib/api";
import type { Source } from "@/lib/types";
export const dynamic = "force-dynamic";
export const metadata = { title: "Your sources", robots: { index: false, follow: false } };
export default async function SourcesSettings() {
  const sources: Source[] = [];
  for (let offset = 0; ; offset += 500) {
    const batch = await getSources(offset, 500);
    sources.push(...batch);
    if (batch.length < 500) break;
  }
  return (
    <UserShell section="account">
      <SourcePreferences sources={sources} />
    </UserShell>
  );
}
