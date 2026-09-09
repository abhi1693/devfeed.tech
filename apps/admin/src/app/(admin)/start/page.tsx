import { redirect } from "next/navigation";
import { initialUserSettings } from "@/lib/server/session";
import { normalizeSettings } from "@/lib/settings";
export default async function StartPage() {
  redirect(normalizeSettings(await initialUserSettings()).defaults.landing_page);
}
