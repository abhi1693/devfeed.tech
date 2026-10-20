import { signInFailure } from "@devfeed/ui/sign-in-feedback";
import { partnerApiOrigin } from "../../lib/server/config";
import { LoginPanel } from "./login-panel";
import type { SignInConfig } from "@devfeed/ui/sign-in";

export const dynamic = "force-dynamic";
export const metadata = { title: "Sign in", robots: { index: false, follow: false } };

export default async function Login({
  searchParams,
}: {
  searchParams: Promise<{ error?: string; signed_out?: string; retry?: string }>;
}) {
  const { error, signed_out, retry } = await searchParams;
  let config: SignInConfig | null = null;
  let unavailable = false;
  try {
    const response = await fetch(`${partnerApiOrigin()}/v1/partner/auth/config`, {
      cache: "no-store",
      redirect: "error",
      signal: AbortSignal.timeout(10_000),
    });
    if (!response.ok) throw new Error("Partner sign-in unavailable");
    config = await response.json();
  } catch {
    unavailable = true;
  }
  return (
    <LoginPanel
      config={config}
      unavailable={unavailable}
      error={
        error === "access_denied"
          ? "Your account needs the partner role to access this portal."
          : signInFailure(error)
      }
      retry={retry}
      signedOut={signed_out === "1"}
    />
  );
}
