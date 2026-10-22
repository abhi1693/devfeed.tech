// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ArticleStackedFrame, ArticleStackedRow } from "@/components/article-stacked";
import { EngagementProvider } from "@/components/article-engagement";
import { userRequest, type UserIdentity } from "@/lib/user";
import type { Article } from "@/lib/types";
import { article, source, topic } from "./fixtures";

const account = vi.hoisted(() => ({
  user: null as UserIdentity | null,
  loading: false,
  profile: null,
  refreshProfile: vi.fn(),
}));
vi.mock("@/components/user-account", () => ({ useUser: () => account }));
vi.mock("@/lib/user", () => ({ userRequest: vi.fn() }));

function Stacked({
  item = article,
  onOpen,
  textOnly = false,
}: {
  item?: Article;
  onOpen?: () => void;
  textOnly?: boolean;
}) {
  return (
    <EngagementProvider articleIds={[item.id]}>
      <ArticleStackedFrame>
        <ArticleStackedRow article={item} onOpen={onOpen} textOnly={textOnly} />
      </ArticleStackedFrame>
    </EngagementProvider>
  );
}

beforeEach(() => {
  account.user = null;
  account.loading = false;
  vi.mocked(userRequest)
    .mockReset()
    .mockImplementation(async (path, init) => {
      const value = { article_id: article.id, opens: 9, likes: 5, liked: false, bookmarked: false };
      if (path.startsWith("engagement?")) return [value];
      if (path.endsWith("/bookmark"))
        return { article_id: article.id, bookmarked: JSON.parse(String(init?.body)).bookmarked };
      if (path.endsWith("/open")) return { ...value, opens: 10 };
      throw new Error("Unexpected request: " + path);
    });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  Reflect.deleteProperty(navigator, "clipboard");
});

it("keeps ordered full-width articles within one named container", () => {
  const second = { ...article, id: "next", slug: "another-article", title: "Next article" };
  render(
    <ArticleStackedFrame>
      <ArticleStackedRow article={article} />
      <ArticleStackedRow article={second} />
    </ArticleStackedFrame>,
  );
  const frame = screen.getByRole("region", { name: "Articles in stacked view" });
  expect(
    within(frame)
      .getAllByRole("article")
      .map((row) => row.getAttribute("aria-labelledby")),
  ).toEqual(
    within(frame)
      .getAllByRole("heading")
      .map((heading) => heading.id),
  );
  expect(
    within(frame)
      .getAllByRole("heading")
      .map((heading) => heading.textContent),
  ).toEqual([article.title, second.title]);
  expect(frame.querySelector(".article-card")).toBeNull();
});

it.each([false, true])(
  "preserves complete Unicode article text and the intended reading order with textOnly=%s",
  (textOnly) => {
    const title = "日本語 🦀 " + "long headline ".repeat(25);
    const description = "A readable explanation of the engineering tradeoffs. ".repeat(8).trim();
    const publisherSource = {
      ...source,
      name: "An engineering publication with a complete descriptive publisher name",
    };
    const aggregator = { ...source, id: "digest", slug: "digest", name: "A discovery digest" };
    const { container } = render(
      <Stacked
        textOnly={textOnly}
        item={{
          ...article,
          title,
          ai_description: description,
          publisher: publisherSource,
          sources: [aggregator, publisherSource],
        }}
      />,
    );
    const publisher = screen.getByRole("link", { name: publisherSource.name });
    const topicLink = screen.getByRole("link", { name: `#${topic.name}` });
    const titleLink = screen.getByRole("link", { name: title.trim() });
    const heading = screen.getByRole("heading", { name: title.trim() });
    const meta = container.querySelector(".article-stacked-meta")!;
    expect(publisher.getAttribute("href")).toBe(`/sources/${source.slug}`);
    expect(topicLink.getAttribute("href")).toBe(`/topics/${topic.slug}`);
    expect(screen.queryByText(aggregator.name)).toBeNull();
    expect(titleLink.textContent).toBe(title);
    expect(publisher.textContent).toBe(publisherSource.name);
    expect(publisher.getAttribute("title")).toBe(publisherSource.name);
    expect(container.querySelector(".article-stacked-description")?.textContent).toBe(description);
    expect(screen.getByRole("group", { name: `Actions for ${title.trim()}` })).toBeTruthy();
    expect(screen.getByRole("button", { name: `Share article: ${title.trim()}` })).toBeTruthy();
    expect(titleLink.getAttribute("href")).toBe(`/articles/${article.slug}`);
    expect(
      (textOnly ? heading.compareDocumentPosition(meta) : meta.compareDocumentPosition(heading)) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(meta.querySelector("time")?.getAttribute("datetime")).toBe(article.published_at);
    expect(meta.querySelectorAll(".article-stacked-topic")).toHaveLength(1);
  },
);

it("uses a compact tag, feed date, and original host when publisher metadata is absent", () => {
  const { container } = render(
    <Stacked
      item={{ ...article, sources: [], topics: [], tags: ["C++ & Web"], published_at: null }}
    />,
  );
  expect(screen.getByText("example.com").closest("a")).toBeNull();
  expect(screen.getByRole("link", { name: "#C++ & Web" }).getAttribute("href")).toBe(
    "/?tag=C%2B%2B%20%26%20Web",
  );
  expect(container.querySelector("time")?.getAttribute("datetime")).toBe(article.feed_at);
});

it("prefers the short description and falls back to safe plain source text", () => {
  const view = render(
    <Stacked
      item={{ ...article, ai_description: "A concise description.", summary: "Source excerpt." }}
    />,
  );
  expect(view.container.querySelector(".article-stacked-description")?.textContent).toBe(
    "A concise description.",
  );
  view.rerender(
    <Stacked
      item={{
        ...article,
        ai_description: " ",
        summary: "## Source **excerpt** with [details](https://example.com) and <b>markup</b>.",
      }}
    />,
  );
  const excerpt = view.container.querySelector(".article-stacked-description")!;
  expect(excerpt.textContent).toBe("Source excerpt with details and markup .");
  expect(excerpt.querySelector("b, a, script")).toBeNull();
});

it.each([
  [false, "AI summary", "An AI overview.", "Source excerpt."],
  [false, "source excerpt", " ", "Source excerpt."],
  [false, "empty summary", " ", " "],
  [true, "AI summary", "An AI overview.", "Source excerpt."],
  [true, "source excerpt", " ", "Source excerpt."],
  [true, "empty summary", " ", " "],
] as const)(
  "keeps one title preview action with textOnly=%s and %s data",
  (textOnly, _summaryKind, ai_summary, summary) => {
    const onOpen = vi.fn();
    const { container } = render(
      <div onClick={(event) => event.preventDefault()}>
        <Stacked item={{ ...article, ai_summary, summary }} textOnly={textOnly} onOpen={onOpen} />
      </div>,
    );
    const title = screen.getByRole("link", { name: article.title });
    expect(
      screen
        .getAllByRole("link")
        .filter((link) => link.getAttribute("href") === `/articles/${article.slug}`),
    ).toEqual([title]);
    expect(screen.queryByRole("link", { name: /^Read (AI summary|summary):/ })).toBeNull();
    expect(container.querySelector(".article-stacked-description")?.textContent ?? "").toBe(
      summary.trim(),
    );
    fireEvent.click(title);
    expect(onOpen).toHaveBeenCalledTimes(1);
  },
);

it("retains a useful thumbnail fallback and no empty description for sparse articles", () => {
  const { container } = render(
    <Stacked
      item={{ ...article, image_url: null, summary: "", ai_summary: null, topics: [], tags: [] }}
    />,
  );
  expect(
    container.querySelector(".article-stacked-thumbnail .image-placeholder")?.textContent,
  ).toBe("Article");
  expect(container.querySelector(".article-stacked-description")).toBeNull();
  expect(container.querySelector(".article-stacked-topic")).toBeNull();
  expect(screen.getByRole("heading", { name: article.title })).toBeTruthy();
  const row = screen.getByRole("article", { name: article.title });
  expect(container.querySelector(".article-stacked-footer")?.parentElement).toBe(row);
  expect(container.querySelector(".article-stacked-copy .article-stacked-actions")).toBeNull();
});

it("uses managed image variants at thumbnail sizes and recovers from image failure", () => {
  const image = "https://images.example.com/cover.webp";
  const { container } = render(
    <ArticleStackedRow
      article={{ ...article, image_url: image, image_variants: [{ url: image, width: 320 }] }}
      priority
    />,
  );
  const thumbnail = container.querySelector(".article-stacked-thumbnail img")!;
  expect(thumbnail.getAttribute("srcset")).toBe(`${image} 320w`);
  expect(thumbnail.getAttribute("sizes")).toBe("(max-width: 556px) 96px, 160px");
  expect(thumbnail.getAttribute("fetchpriority")).toBe("high");
  expect(thumbnail.getAttribute("loading")).toBe("eager");
  fireEvent.error(thumbnail);
  expect(container.querySelector(".article-stacked-thumbnail img")).toBeNull();
  expect(container.querySelector(".image-placeholder")).toBeTruthy();
});

it.each([false, true])(
  "retains attribution and article actions with textOnly=%s while controlling article and publisher images",
  (textOnly) => {
    const publisher = { ...source, logo_url: "https://images.example.test/publisher.webp" };
    const cover = "https://images.example.test/article.webp";
    const { container } = render(
      <ArticleStackedRow
        article={{ ...article, publisher, sources: [publisher], image_url: cover }}
        textOnly={textOnly}
      />,
    );
    if (textOnly) {
      expect(container.querySelector("img")).toBeNull();
    } else {
      expect(container.querySelector(".article-stacked-thumbnail img")?.getAttribute("src")).toBe(
        cover,
      );
      expect(container.querySelector(".source-avatar img")?.getAttribute("src")).toBe(
        publisher.logo_url,
      );
    }
    expect(screen.getByRole("link", { name: publisher.name })).toBeTruthy();
    expect(screen.getByRole("link", { name: article.title })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Read original article in a new tab" })).toBeTruthy();
    expect(screen.getByRole("button", { name: `Share article: ${article.title}` })).toBeTruthy();
  },
);

it.each([false, true])(
  "keeps preview actions distinct from source, save, share, and outbound actions with textOnly=%s",
  async (textOnly) => {
    const onOpen = vi.fn();
    render(
      <div onClick={(event) => event.preventDefault()}>
        <Stacked onOpen={onOpen} textOnly={textOnly} />
      </div>,
    );
    fireEvent.click(screen.getByRole("link", { name: source.name }));
    fireEvent.click(screen.getByRole("link", { name: `#${topic.name}` }));
    fireEvent.click(screen.getByRole("link", { name: "Sign in to save article for later" }));
    fireEvent.click(screen.getByRole("button", { name: `Share article: ${article.title}` }));
    fireEvent.click(screen.getByRole("link", { name: "Read original article in a new tab" }));
    expect(onOpen).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("link", { name: article.title }));
    expect(onOpen).toHaveBeenCalledTimes(1);
    await screen.findByRole("img", { name: "10 clicks to the original article" });
  },
);

it("copies the shared article route from the existing share popover", async () => {
  const copy = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: copy } });
  render(<Stacked />);
  fireEvent.click(screen.getByRole("button", { name: `Share article: ${article.title}` }));
  fireEvent.click(screen.getByRole("button", { name: "Copy link" }));
  await screen.findByText("Link copied.");
  expect(copy).toHaveBeenCalledWith(`${window.location.origin}/articles/${article.slug}`);
});

it("uses the existing authenticated bookmark contract and recovers from a failed save", async () => {
  account.user = {
    user_id: "reader",
    csrf_token: "csrf",
    name: "Reader",
    email: null,
    expires_at: 4102444800,
  };
  render(<Stacked />);
  const save = screen.getByRole("button", { name: "Save article for later" });
  await waitFor(() => expect(save).toHaveProperty("disabled", false));
  vi.mocked(userRequest).mockRejectedValueOnce(new Error("Offline"));
  fireEvent.click(save);
  expect((await screen.findByRole("alert")).textContent).toContain("Couldn’t update bookmark");
  expect(screen.queryByRole("button", { name: "Remove bookmark" })).toBeNull();
  fireEvent.click(save);
  await screen.findByRole("button", { name: "Remove bookmark" });
  expect(userRequest).toHaveBeenLastCalledWith(
    `articles/${article.id}/bookmark`,
    expect.objectContaining({
      method: "PUT",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": "csrf" },
      body: JSON.stringify({ bookmarked: true }),
    }),
  );
});

it("preserves permanent sponsorship and paid link qualification", () => {
  render(
    <Stacked item={{ ...article, sponsor_names: ["Partner"], sponsor_link_rel: ["external"] }} />,
  );
  const disclosure = screen.getByText("Sponsored");
  const title = screen.getByRole("link", { name: article.title });
  expect(disclosure.getAttribute("title")).toBe("Sponsored by Partner");
  expect(title.getAttribute("aria-describedby")).toBe(disclosure.id);
  expect(title.getAttribute("rel")).toBe("external sponsored");
  expect(screen.queryByRole("link", { name: /^Read (AI summary|summary):/ })).toBeNull();
  expect(
    screen.getByRole("link", { name: "Read original article in a new tab" }).getAttribute("rel"),
  ).toBe("external sponsored noopener noreferrer");
});

it("does not expose an unsafe canonical URL as an original-article action", () => {
  render(<Stacked item={{ ...article, sources: [], canonical_url: "javascript:alert(1)" }} />);
  expect(screen.queryByRole("link", { name: "Read original article in a new tab" })).toBeNull();
  expect(screen.getByText("Original publisher")).toBeTruthy();
});

it("keeps sponsored rows stable and inert while background delivery is suspended", async () => {
  const content = (active: boolean, visible: boolean) => (
    <ArticleStackedFrame>
      <ArticleStackedRow
        article={{ ...article, sponsor_names: ["Partner"] }}
        promotion={{ productId: "product", active, visible, preserveSpace: true }}
      />
    </ArticleStackedFrame>
  );
  const view = render(content(true, true));
  const row = screen.getByRole("article", { name: article.title });
  const title = screen.getByRole("link", { name: article.title });
  view.rerender(content(false, true));
  expect(row.getAttribute("aria-hidden")).toBe("true");
  expect(row.hasAttribute("inert")).toBe(true);
  expect(row.hasAttribute("hidden")).toBe(false);
  expect(row.getAttribute("data-suspended")).toBeNull();
  view.rerender(content(false, false));
  expect(row.getAttribute("data-suspended")).toBe("true");
  view.rerender(content(true, true));
  expect(screen.getByRole("link", { name: article.title })).toBe(title);
  expect(screen.getByRole("article", { name: article.title })).toBe(row);
  await act(async () => {});
});
