import type { Metadata } from "next";
import { UserShell } from "@/components/user-shell";
import { FeedSettings } from "@/components/feed-settings";

export const metadata: Metadata = {
  title: "Feed settings",
  robots: { index: false, follow: false },
};
export default function FeedSettingsPage() {
  return (
    <UserShell section="account">
      <FeedSettings />
    </UserShell>
  );
}
