import ReaderLink from "@/components/reader-link";
import { ReaderSurface } from "./reader-context";
import { readerPromptPolicies } from "@/lib/reader-context";

export function ReaderNotFound() {
  return (
    <main className="standalone-state">
      <ReaderSurface promptEligibility={readerPromptPolicies.quiet} />
      <p className="eyebrow">Nothing here just yet</p>
      <h1>Page not found</h1>
      <p>This article or page may no longer be available.</p>
      <ReaderLink className="button primary" href="/">
        Discover something else
      </ReaderLink>
    </main>
  );
}
