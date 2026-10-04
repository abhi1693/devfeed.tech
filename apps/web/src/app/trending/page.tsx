import type { Metadata } from "next";
import { TrendingContent } from "@/components/trending-content";
import type { SearchParams } from "@/lib/feed-query";
import { getTrending } from "@/lib/api";
export const dynamic = "force-dynamic";
export const metadata: Metadata = {
  title: "Trending articles",
  robots: { index: false, follow: false },
  description: "Developer articles users are opening and liking this week.",
};
export default async function Trending({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const query = await searchParams;
  const cursor = typeof query.cursor === "string" ? query.cursor : "";
  const result = await Promise.allSettled([getTrending(cursor)]);
  const feed = result[0];
  return <TrendingContent feed={feed} cursor={cursor} />;
}
