import { redirect } from "next/navigation";
import { LoginPanel } from "@/components/organisms/login-panel";
import { LoginLayout } from "@/components/templates/login-layout";
import { authConfiguration, currentAdmin } from "@/lib/server/session";

export const dynamic = "force-dynamic";
export const metadata = { title: "Sign in" };

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string; signed_out?: string }>;
}) {
  let enabled = false;
  let unavailable = false;
  let admin = null;
  try {
    admin = await currentAdmin();
    enabled = (await authConfiguration()).enabled;
  } catch {
    unavailable = true;
  }
  if (admin) redirect("/");
  const { error, signed_out } = await searchParams;
  const message = unavailable
    ? "Cannot reach the admin service. Please try again shortly."
    : error === "access_denied"
      ? "Your account was authenticated, but the required admin role was not granted. You are not signed in to DevFeed."
      : error
        ? "Sign-in was not completed. Please try again."
        : undefined;
  return (
    <LoginLayout>
      <LoginPanel enabled={enabled} error={message} signedOut={!message && signed_out === "1"} />
    </LoginLayout>
  );
}
