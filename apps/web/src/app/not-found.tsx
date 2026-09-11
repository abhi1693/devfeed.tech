import Link from "next/link";
export default function NotFound() {
  return (
    <main className="standalone-state">
      <p className="eyebrow">Nothing here just yet</p>
      <h1>Page not found</h1>
      <p>This article or page may no longer be available.</p>
      <Link className="button primary" href="/">
        Discover something else
      </Link>
    </main>
  );
}
