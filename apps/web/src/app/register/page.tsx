import { redirect } from "next/navigation";
import type { Metadata } from "next";
export const metadata: Metadata = {
  title: "Create account",
  robots: { index: false, follow: false },
};
export default function Register() {
  redirect("/api/v1/user/auth/login?register=true");
}
