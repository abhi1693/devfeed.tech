import { redirect } from "next/navigation";
import type { Metadata } from "next";
export const metadata: Metadata = {
  title: "Sign in or create an account",
  robots: { index: false, follow: false },
};
export default function Register() {
  redirect("/login");
}
