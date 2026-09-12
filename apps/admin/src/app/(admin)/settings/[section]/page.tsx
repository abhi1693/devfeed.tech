import { notFound } from "next/navigation";
import { SettingsPage } from "@/components/organisms/settings-page";
import { settingsSections } from "@/lib/settings";
type Props = { params: Promise<{ section: string }> };
export async function generateMetadata({ params }: Props) {
  const { section } = await params;
  return {
    title: `${settingsSections.find((item) => item.id === section)?.label ?? "Account"} settings`,
  };
}
export default async function Page({ params }: Props) {
  const { section: id } = await params;
  const current = settingsSections.find((item) => item.id === id);
  if (!current) notFound();
  return <SettingsPage section={current.id} />;
}
