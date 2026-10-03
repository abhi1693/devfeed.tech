import { PartnerRuns } from "@/components/organisms/partner-runs";
export const metadata = { title: "Pipeline jobs" };
export default function Page() {
  return <PartnerRuns kind="pipeline" />;
}
