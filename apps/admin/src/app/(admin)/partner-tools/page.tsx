import type { Metadata } from "next";
import { PartnerTools } from "@/components/organisms/partner-tools";

export const metadata: Metadata = { title: "Partner tools" };
export default function PartnerToolsPage() {
  return <PartnerTools />;
}
