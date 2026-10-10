import { PartnerConnectionPage } from "@/components/organisms/partner-connection-page";
export const metadata = { title: "Partner details" };
export default async function Page({ params }: { params: Promise<{ provider: string }> }) {
  const { provider } = await params;
  return <PartnerConnectionPage key={provider} provider={provider} />;
}
