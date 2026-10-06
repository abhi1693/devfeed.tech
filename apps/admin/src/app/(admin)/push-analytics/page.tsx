import type { Metadata } from "next";
import { PushAnalyticsOverview } from "@/components/organisms/push-analytics";

export const metadata: Metadata = { title: "Push analytics" };

export default function PushAnalyticsPage() {
  return <PushAnalyticsOverview />;
}
