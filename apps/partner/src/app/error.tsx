"use client";

import { Button } from "@devfeed/ui/button";

export default function ErrorPage({ reset }: { reset: () => void }) {
  return (
    <main className="login">
      <h1>Could not load your partner portal</h1>
      <p>Please retry in a moment.</p>
      <Button onClick={reset}>Retry</Button>
    </main>
  );
}
