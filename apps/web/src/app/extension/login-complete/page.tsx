import type { Metadata } from "next";
import { headers } from "next/headers";

export const metadata: Metadata = {
  title: "Sign-in complete",
  robots: { index: false, follow: false },
};

export default async function ExtensionLoginComplete() {
  const nonce = (await headers()).get("content-security-policy")?.match(/'nonce-([^']+)'/)?.[1];
  return (
    <main className="standalone-state" aria-live="polite">
      <script nonce={nonce} dangerouslySetInnerHTML={{ __html: "window.close()" }} />
      <h1>Sign-in complete</h1>
      <p>You can return to DevFeed.</p>
    </main>
  );
}
