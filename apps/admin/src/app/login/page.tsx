import { signInFailure } from "@devfeed/ui/sign-in-feedback";
import { redirect } from "next/navigation";
import { LoginPanel } from "@/components/organisms/login-panel";
import type { SignInProvider } from "@devfeed/ui/sign-in";
import { authConfiguration, currentAdmin } from "@/lib/server/session";

export const dynamic = "force-dynamic";
export const metadata = { title: "Sign in" };

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string; signed_out?: string; retry?: string }>;
}) {
  let enabled = false;
  let providers: SignInProvider[] = [];
  let unavailable = false;
  let admin = null;
  try {
    admin = await currentAdmin();
    const config = await authConfiguration();
    enabled = config.enabled;
    providers = config.providers ?? [];
  } catch {
    unavailable = true;
  }
  if (admin) redirect("/");
  const { error, signed_out, retry } = await searchParams;
  const message = unavailable
    ? "Cannot reach the admin service. Please try again shortly."
    : error === "access_denied"
      ? "Your account was authenticated, but the required admin role was not granted. You are not signed in to DevFeed."
      : signInFailure(error);
  return (
    <LoginPanel
      enabled={enabled}
      providers={providers}
      error={message}
      retry={retry}
      signedOut={!message && signed_out === "1"}
    />
  );
}
