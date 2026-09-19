import type { Metadata } from "next";
import { SearchAnalyticsOverview } from "@/components/organisms/search-analytics";

export const metadata: Metadata = { title: "Search analytics" };

export default function SearchAnalyticsPage() {
  return <SearchAnalyticsOverview />;
}
