import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { UserShell } from "@/components/user-shell";
import { UserLoginError } from "@/components/user-login";
export const metadata: Metadata = {
  title: "Sign in",
  robots: { index: false, follow: false },
};
export default async function Login({
  searchParams,
}: {
  searchParams: Promise<{ error?: string }>;
}) {
  if (!(await searchParams).error) redirect("/api/v1/user/auth/login");
  return (
    <UserShell section="account">
      <UserLoginError />
    </UserShell>
  );
}
