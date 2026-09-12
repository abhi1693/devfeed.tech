import type { Metadata } from "next";
import { QueuesOverview } from "@/components/organisms/workers";

export const metadata: Metadata = { title: "Queues" };
export default function QueuesPage() {
  return <QueuesOverview />;
}
