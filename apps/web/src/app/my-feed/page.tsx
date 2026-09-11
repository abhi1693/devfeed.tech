import type { Metadata } from "next";
import { UserShell } from "@/components/user-shell";
import { PersonalFeed } from "@/components/personal-feed";
export const metadata: Metadata = {
  title: "My feed",
  robots: { index: false, follow: false },
};
export default async function MyFeed({
  searchParams,
}: {
  searchParams: Promise<{ cursor?: string }>;
}) {
  const { cursor } = await searchParams;
  return (
    <UserShell section="personal">
      <PersonalFeed cursor={typeof cursor === "string" ? cursor : undefined} />
    </UserShell>
  );
}
