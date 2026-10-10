import { PartnerConnectionPage } from "@/components/organisms/partner-connection-page";
export const metadata = { title: "Edit partner" };
export default async function Page({ params }: { params: Promise<{ provider: string }> }) {
  const { provider } = await params;
  return <PartnerConnectionPage key={provider} provider={provider} editing />;
}
