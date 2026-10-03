import type { Metadata } from "next";
import { Leaderboard } from "@/components/leaderboard";
import { UserShell } from "@/components/user-shell";

export const metadata: Metadata = { title: "Leaderboard", robots: { index: false, follow: true } };

export default function Page() {
  return (
    <UserShell section="leaderboard">
      <Leaderboard />
    </UserShell>
  );
}
