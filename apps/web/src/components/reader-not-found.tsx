import ReaderLink from "@/components/reader-link";

export function ReaderNotFound() {
  return (
    <main className="standalone-state">
      <p className="eyebrow">Nothing here just yet</p>
      <h1>Page not found</h1>
      <p>This article or page may no longer be available.</p>
      <ReaderLink className="button primary" href="/">
        Discover something else
      </ReaderLink>
    </main>
  );
}
