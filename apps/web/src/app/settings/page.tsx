import { redirect } from "next/navigation";
export const metadata = { title: "Settings", robots: { index: false, follow: false } };
export default function Settings() {
  redirect("/settings/profile");
}
