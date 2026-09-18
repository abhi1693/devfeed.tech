import { UserShell } from "@/components/user-shell";
import { SourcePreferences } from "@/components/source-preferences";
export const dynamic = "force-dynamic";
export const metadata = { title: "Your sources", robots: { index: false, follow: false } };
export default function SourcesSettings() {
  return (
    <UserShell section="account">
      <SourcePreferences />
    </UserShell>
  );
}
