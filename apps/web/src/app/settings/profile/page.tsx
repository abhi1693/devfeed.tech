import type { Metadata } from "next";
import { UserShell } from "@/components/user-shell";
import { ProfileSettings } from "@/components/profile-settings";
export const metadata: Metadata = {
  title: "Profile settings",
  robots: { index: false, follow: false },
};
export default function Profile() {
  return (
    <UserShell section="account">
      <ProfileSettings />
    </UserShell>
  );
}
