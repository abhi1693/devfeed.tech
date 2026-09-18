import { UserShell } from "@/components/user-shell";
import { TopicPreferences } from "@/components/topic-preferences";
export const dynamic = "force-dynamic";
export const metadata = { title: "Your topics", robots: { index: false, follow: false } };
export default function TopicsSettings() {
  return (
    <UserShell section="account">
      <TopicPreferences />
    </UserShell>
  );
}
