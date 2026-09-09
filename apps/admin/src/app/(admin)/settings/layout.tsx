import { SettingsNav } from "@/components/molecules/settings-nav";
export default function SettingsLayout({ children }: { children: React.ReactNode }) {
  return <div className="w-full min-w-0 space-y-5"><SettingsNav />{children}</div>;
}
