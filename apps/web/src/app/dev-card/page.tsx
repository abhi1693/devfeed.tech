import type { Metadata } from "next";
import { UserShell } from "@/components/user-shell";
import { DevCardCreator } from "@/components/dev-card-creator";

export const metadata: Metadata = {
  title: "Create your Dev Card",
  description: "Your stack. Your reading journey. Preview your own DevFeed card.",
};
export default function Page() {
  return (
    <UserShell>
      <DevCardCreator />
    </UserShell>
  );
}
