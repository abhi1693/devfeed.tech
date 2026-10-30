import Link from "./reader-link";
import { GuestHomeVisibility } from "./guest-home-visibility";

/** Static public copy: no account identity, extra reads, images or scripts. */
export function GuestHomeIntro() {
  return (
    <GuestHomeVisibility>
      <section className="guest-home-intro" aria-labelledby="guest-home-title">
        <h1 id="guest-home-title">Developer news, tutorials &amp; releases</h1>
        <p>
          Explore <Link href="/topics">topics</Link> and <Link href="/sources">sources</Link>, read
          AI overviews, and follow links to the original publishers.
        </p>
        <p>
          Read without an account. Sign in to follow interests, save articles and personalize your
          feed, or connect your AI tools with <Link href="/mcp">MCP</Link>.
        </p>
      </section>
    </GuestHomeVisibility>
  );
}
