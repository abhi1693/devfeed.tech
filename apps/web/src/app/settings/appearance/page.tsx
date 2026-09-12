import type { Metadata } from "next";
import { UserShell } from "@/components/user-shell";
import { AppearanceSettings } from "@/components/appearance-settings";

export const metadata: Metadata = {
  title: "Appearance settings",
  robots: { index: false, follow: false },
};
export default function AppearancePage() {
  return (
    <UserShell section="account">
      <AppearanceSettings />
    </UserShell>
  );
}
