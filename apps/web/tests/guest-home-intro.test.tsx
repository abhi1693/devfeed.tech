// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { GuestHomeIntro } from "@/components/guest-home-intro";
import { ReaderContextProvider, ReaderSurface } from "@/components/reader-context";
import { readerPromptPolicies, type ArticleIdentity } from "@/lib/reader-context";

const session = vi.hoisted(() => ({ user: null as { user_id: string } | null, loading: true }));
vi.mock("@/components/user-account", () => ({ useUser: () => session }));
afterEach(cleanup);
beforeEach(() => {
  session.user = null;
  session.loading = true;
});

it("renders useful public copy and crawlable links before account hydration", () => {
  const html = renderToStaticMarkup(<GuestHomeIntro />);
  expect(html).toContain('<h1 id="guest-home-title">Developer news, tutorials &amp; releases</h1>');
  for (const path of ["/topics", "/sources", "/mcp"]) expect(html).toContain(`href="${path}"`);
  expect(html).toContain("AI overviews");
  expect(html).toContain("original publishers");
  expect(html).toContain("Read without an account");
  expect(html).toContain("save articles");
  expect(html).not.toMatch(/<img|<script|reader@example|csrf/i);
});

it("follows account and foreground preview changes without changing the public copy", () => {
  function Home({ article = null }: { article?: ArticleIdentity | null }) {
    return (
      <ReaderContextProvider>
        <ReaderSurface
          currentArticle={article}
          promptEligibility={readerPromptPolicies.quiet}
          priority="foreground"
        />
        <GuestHomeIntro />
      </ReaderContextProvider>
    );
  }
  const { rerender } = render(<Home />);
  const intro = () =>
    screen.queryByRole("region", { name: "Developer news, tutorials & releases" });
  expect(intro()).toBeTruthy();
  session.user = { user_id: "member" };
  session.loading = false;
  rerender(<Home />);
  expect(intro()).toBeNull();
  session.user = null;
  rerender(<Home />);
  expect(intro()).toBeTruthy();
  rerender(<Home article={{ id: "article", slug: "read-the-original" }} />);
  expect(intro()).toBeNull();
  rerender(<Home />);
  expect(intro()).toBeTruthy();
});
