"use client";
import Link from "next/link";
import { useState } from "react";
import { RetryButton } from "@devfeed/ui/retry-button";

export default function UserError() {
  const [retrying, setRetrying] = useState(false);
  return (
    <main className="standalone-state">
      <p className="eyebrow">A brief interruption</p>
      <h1>This page couldn’t load</h1>
      <p>Please try again in a moment.</p>
      <div className="pagination">
        <RetryButton
          pending={retrying}
          onRetry={() => {
            setRetrying(true);
            // Retry the server request as well as the client error boundary.
            window.location.reload();
          }}
        />
        <Link className="button" href="/">
          Back to the feed
        </Link>
      </div>
    </main>
  );
}
