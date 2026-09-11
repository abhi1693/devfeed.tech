import { UserShell } from "@/components/user-shell";
import { UserSettingsLayout } from "@/components/user-settings-layout";
import { LoadingSkeleton } from "@/components/loading-skeleton";

export default function Loading() {
  return <UserShell section="account">
    <UserSettingsLayout section="topics">
      <section className="profile-panel">
        <LoadingSkeleton kind="topics" label="Loading your topics…" />
      </section>
    </UserSettingsLayout>
  </UserShell>;
}
