import { UserShell } from "@/components/user-shell";
import { UserSettingsLayout } from "@/components/user-settings-layout";
import { LoadingSkeleton } from "@/components/loading-skeleton";

export default function Loading() {
  return <UserShell section="account">
    <UserSettingsLayout section="sources">
      <section className="profile-panel">
        <LoadingSkeleton kind="sources" label="Loading your sources…" />
      </section>
    </UserSettingsLayout>
  </UserShell>;
}
