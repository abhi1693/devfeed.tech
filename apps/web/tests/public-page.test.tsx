import { expect, it, vi } from "vitest";
import { Suspense, type ReactElement } from "react";
import Sources from "@/app/sources/page";
import Topics from "@/app/topics/page";
import { PublicPage } from "@/components/public-page";
import { PublicPageLoading } from "@/components/public-page-loading";
import { UserShell } from "@/components/user-shell";
import { parseFilters } from "@/lib/feed-query";
import { getSources, getTopics } from "@/lib/api";
import { source, topic } from "./fixtures";

vi.mock("@/lib/api", () => ({ getSources: vi.fn(), getTopics: vi.fn() }));
it.each([
  [Sources, getSources, source, "sources"],
  [Topics, getTopics, topic, "topics"],
] as const)(
  "streams catalog results without waiting for the upstream %#",
  async (Page, load, item, kind) => {
    let release!: () => void;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    vi.mocked(getSources).mockReturnValue(held.then(() => [source]));
    vi.mocked(getTopics).mockReturnValue(held.then(() => [topic]));
    const page = await Page({ searchParams: Promise.resolve({ offset: "60" }) });
    expect(page.props.kind).toBe(kind);
    expect(load).toHaveBeenCalledWith(...(kind === "sources" ? [60, 60] : [60]));
    release();
    const result = await page.props.content;
    expect(result.props[kind]).toEqual([item]);
    expect(result.props.offset).toBe(60);
    expect(result.props.withShell).toBe(false);
  },
);
it("keeps the fallback separate from the eventual public content and propagates failures", async () => {
  const content = Promise.resolve(<p>Ready</p>);
  const filters = parseFilters({ q: "existing search" });
  const shell = PublicPage({ content, filters });
  expect(shell.type).toBe(UserShell);
  expect(shell.props.filters).toEqual(filters);
  const boundary = shell.props.children;
  expect(boundary.type).toBe(Suspense);
  expect(boundary.props.fallback.type).toBe(PublicPageLoading);
  const child = boundary.props.children as ReactElement<
    { content: Promise<ReactElement> },
    (props: { content: Promise<ReactElement> }) => Promise<ReactElement>
  >;
  expect(await child.type(child.props)).toEqual(<p>Ready</p>);
  await expect(child.type({ content: Promise.reject(new Error("unavailable")) })).rejects.toThrow(
    "unavailable",
  );
});
