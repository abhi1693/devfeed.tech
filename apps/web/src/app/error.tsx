"use client";
import Link from "next/link";
export default function UserError({ reset }: { reset: () => void }) {
  return (
    <main className="standalone-state">
      <p className="eyebrow">A brief interruption</p>
      <h1>This page couldn’t load</h1>
      <p>Please try again in a moment.</p>
      <div className="pagination">
        <button className="button primary" onClick={reset}>
          Try again
        </button>
        <Link className="button" href="/">
          Back to the feed
        </Link>
      </div>
    </main>
  );
}
