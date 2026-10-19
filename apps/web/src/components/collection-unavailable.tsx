import { UserShell } from "./user-shell";
import { RetryFeed } from "./retry-feed";

export function CollectionUnavailable({ kind }: { kind: "sources" | "topics" | "tags" }) {
  return (
    <UserShell section={kind}>
      <section className="empty-state" role="alert">
        <h1>This page couldn’t load</h1>
        <p>The service is temporarily unavailable. Please try again in a moment.</p>
        <RetryFeed />
      </section>
    </UserShell>
  );
}
