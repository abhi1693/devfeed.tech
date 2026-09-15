import { redirect } from "next/navigation";
export default async function LegacyMyFeed({
  searchParams,
}: {
  searchParams: Promise<{ cursor?: string }>;
}) {
  const { cursor } = await searchParams;
  redirect(typeof cursor === "string" ? `/?cursor=${encodeURIComponent(cursor)}` : "/");
}
