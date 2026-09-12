import type { Metadata } from "next";
import { UserShell } from "@/components/user-shell";
import { NotificationSettings } from "@/components/notification-settings";
export const metadata: Metadata = {
  title: "Notification settings",
  robots: { index: false, follow: false },
};
export default function Notifications() {
  return (
    <UserShell section="account">
      <NotificationSettings />
    </UserShell>
  );
}
