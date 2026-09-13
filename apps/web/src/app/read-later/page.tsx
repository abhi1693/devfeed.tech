import type { Metadata } from "next";
import { UserShell } from "@/components/user-shell";
import { ReadLater } from "@/components/read-later";

export const metadata: Metadata = { title: "Read later", robots: { index: false, follow: false } };
export default async function ReadLaterPage({
  searchParams,
}: {
  searchParams: Promise<{ cursor?: string }>;
}) {
  const { cursor } = await searchParams;
  return (
    <UserShell section="bookmarks">
      <ReadLater cursor={typeof cursor === "string" ? cursor : undefined} />
    </UserShell>
  );
}
