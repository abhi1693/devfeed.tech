import type { Metadata } from "next";
import { UserLogin } from "@/components/user-login";
export const metadata: Metadata = {
  title: "Sign in",
  robots: { index: false, follow: false },
};
export default async function Login({
  searchParams,
}: {
  searchParams: Promise<{ error?: string; return_to?: string }>;
}) {
  const params = await searchParams;
  return <UserLogin returnTo={params.return_to || "/"} error={Boolean(params.error)} />;
}
