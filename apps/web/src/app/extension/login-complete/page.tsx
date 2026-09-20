import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Sign-in complete",
  robots: { index: false, follow: false },
};

export default function ExtensionLoginComplete() {
  return (
    <main className="standalone-state" aria-live="polite">
      <script dangerouslySetInnerHTML={{ __html: "window.close()" }} />
      <h1>Sign-in complete</h1>
      <p>You can return to DevFeed.</p>
    </main>
  );
}
