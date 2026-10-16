import type { Metadata } from "next";
import { UserLogin } from "@/components/user-login";
export const metadata: Metadata = {
  title: "Sign in",
  robots: { index: false, follow: false },
};
export default async function Login({
  searchParams,
}: {
  searchParams: Promise<{ error?: string | string[]; return_to?: string | string[] }>;
}) {
  const params = await searchParams;
  return (
    <UserLogin
      returnTo={typeof params.return_to === "string" ? params.return_to : "/"}
      error={Boolean(params.error)}
    />
  );
}
