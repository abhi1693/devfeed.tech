import type { Metadata } from "next";
import { WorkersOverview } from "@/components/organisms/workers";

export const metadata: Metadata = { title: "Workers" };
export default function WorkersPage() { return <WorkersOverview />; }
