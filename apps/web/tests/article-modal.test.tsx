// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useEffect } from "react";
import { renderToString } from "react-dom/server";
import {
  ArticleNavigationProvider,
  useArticleNavigation,
  type ArticleSequence,
} from "@/components/article-navigation";
import { ArticleModal } from "@/components/article-modal";
import { WebArticleNavigationProvider } from "@/components/web-article-navigation";
import {
  ReaderContextProvider,
  ReaderSurface,
  useReaderContext,
} from "@/components/reader-context";
import { readerPromptPolicies, type ArticleIdentity } from "@/lib/reader-context";
const { back, replace, slots } = vi.hoisted(() => ({
  back: vi.fn(),
  replace: vi.fn(),
  slots: { page: [] as string[], modal: [] as string[] },
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ back, replace }),
  useSelectedLayoutSegments: (slot?: string) => (slot === "modal" ? slots.modal : slots.page),
}));
beforeEach(() => {
  slots.page = [];
  slots.modal = [];
  Object.defineProperty(HTMLDialogElement.prototype, "showModal", {
    configurable: true,
    value: function (this: HTMLDialogElement) {
      this.setAttribute("open", "");
    },
  });
  Object.defineProperty(HTMLDialogElement.prototype, "close", {
    configurable: true,
    value: function (this: HTMLDialogElement) {
      this.removeAttribute("open");
    },
  });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  Reflect.deleteProperty(Element.prototype, "animate");
  back.mockClear();
  replace.mockClear();
});

it("opens a native modal and restores scrolling and focus on dismissal", () => {
  const show = vi.spyOn(HTMLDialogElement.prototype, "showModal").mockImplementation(function (
    this: HTMLDialogElement,
  ) {
    this.setAttribute("open", "");
  });
  vi.spyOn(HTMLDialogElement.prototype, "close").mockImplementation(function (
    this: HTMLDialogElement,
  ) {
    this.removeAttribute("open");
  });
  const trigger = document.createElement("button");
  document.body.append(trigger);
  trigger.focus();
  const view = render(
    <ArticleModal active slug="first">
      <h1>Article</h1>
    </ArticleModal>,
  );
  expect(show).toHaveBeenCalledOnce();
  expect(document.body.style.overflow).toBe("hidden");
  fireEvent.click(screen.getByRole("button", { name: "Close preview" }));
  expect(back).toHaveBeenCalledOnce();
  expect(document.querySelector("dialog[open]")).toBeNull();
  expect(document.body.style.overflow).toBe("");
  expect(document.activeElement).toBe(trigger);
  view.unmount();
  trigger.remove();
});

it("releases an invisible modal even when the exit animation never finishes", async () => {
  vi.useFakeTimers();
  Object.defineProperty(Element.prototype, "animate", {
    configurable: true,
    value: () => ({ finished: new Promise(() => {}), cancel: vi.fn() }),
  });
  try {
    render(
      <ArticleModal active slug="first">
        Preview
      </ArticleModal>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Close preview" }));
    expect(document.querySelector("dialog[open]")).not.toBeNull();
    await act(async () => vi.advanceTimersByTime(200));
    expect(document.querySelector("dialog[open]")).toBeNull();
    expect(document.body.style.overflow).toBe("");
    expect(back).toHaveBeenCalledOnce();
  } finally {
    vi.useRealTimers();
  }
});
it("handles Escape through browser history", () => {
  vi.spyOn(HTMLDialogElement.prototype, "showModal").mockImplementation(function (
    this: HTMLDialogElement,
  ) {
    this.setAttribute("open", "");
  });
  vi.spyOn(HTMLDialogElement.prototype, "close").mockImplementation(() => {});
  render(
    <ArticleModal active slug="first">
      Preview
    </ArticleModal>,
  );
  fireEvent(screen.getByRole("dialog"), new Event("cancel", { bubbles: true, cancelable: true }));
  expect(back).toHaveBeenCalledOnce();
});

it("contains Tab and Shift+Tab focus, including repeated keys", () => {
  vi.spyOn(HTMLElement.prototype, "getClientRects").mockReturnValue([{}] as unknown as DOMRectList);
  render(
    <ArticleModal active slug="first">
      <button>Save</button>
    </ArticleModal>,
  );
  const first = screen.getByRole("button", { name: "Close preview" });
  const last = screen.getByRole("button", { name: "Save" });
  last.focus();
  expect(fireEvent.keyDown(last, { key: "Tab", repeat: true })).toBe(false);
  expect(document.activeElement).toBe(first);
  expect(fireEvent.keyDown(first, { key: "Tab", shiftKey: true })).toBe(false);
  expect(document.activeElement).toBe(last);
  expect(fireEvent.keyDown(last, { key: "Tab", ctrlKey: true })).toBe(true);
  expect(document.activeElement).toBe(last);
});

it("closes direct article links onto the feed instead of leaving the site", () => {
  render(
    <ArticleModal active direct slug="first">
      Preview
    </ArticleModal>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Close preview" }));
  expect(replace).toHaveBeenCalledWith("/", { scroll: true });
  expect(back).not.toHaveBeenCalled();
});

function Sequence({ value, children }: { value: ArticleSequence; children: React.ReactNode }) {
  const { setSequence } = useArticleNavigation();
  useEffect(() => {
    setSequence(value);
  }, [value, setSequence]);
  return children;
}
function preview(
  slugs: string[],
  loadMore: ArticleSequence["loadMore"] = async () => undefined,
  hasMore = false,
) {
  return render(
    <ArticleNavigationProvider
      overlay={{ slug: "first", entry: "intercepted", dismiss: "history" }}
    >
      <Sequence value={{ slugs, hasMore, loading: false, loadMore }}>
        <ArticleModal active slug="first">
          <h1>First</h1>
          <input aria-label="Search topics" />
        </ArticleModal>
      </Sequence>
    </ArticleNavigationProvider>,
  );
}
it("navigates in feed order with buttons and unmodified arrow keys without adding history entries", () => {
  preview(["before", "first", "after"]);
  fireEvent.click(screen.getByRole("button", { name: "Next article" }));
  expect(replace).toHaveBeenLastCalledWith("/articles/after", { scroll: false });
  fireEvent.keyDown(screen.getByRole("dialog"), { key: "ArrowLeft" });
  expect(replace).toHaveBeenLastCalledWith("/articles/before", { scroll: false });
  replace.mockClear();
  fireEvent.keyDown(screen.getByRole("textbox"), { key: "ArrowRight" });
  fireEvent.keyDown(screen.getByRole("dialog"), { key: "ArrowRight", altKey: true });
  fireEvent.keyDown(screen.getByRole("dialog"), { key: "ArrowRight", repeat: true });
  expect(replace).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Close preview" }));
  expect(back).toHaveBeenCalledOnce();
});
it("disables navigation at feed boundaries and when the article is outside the sequence", () => {
  const view = preview(["first"]);
  expect(
    (screen.getByRole("button", { name: "Previous article" }) as HTMLButtonElement).disabled,
  ).toBe(true);
  expect((screen.getByRole("button", { name: "Next article" }) as HTMLButtonElement).disabled).toBe(
    true,
  );
  view.unmount();
  preview(["unrelated"]);
  fireEvent.keyDown(screen.getByRole("dialog"), { key: "ArrowRight" });
  expect(replace).not.toHaveBeenCalled();
});
it("loads the next feed page before navigating and does not reopen a dismissed preview", async () => {
  let done!: (slug: string) => void;
  const load = vi.fn(
    () =>
      new Promise<string>((resolve) => {
        done = resolve;
      }),
  );
  preview(["first"], load, true);
  fireEvent.click(screen.getByRole("button", { name: "Next article" }));
  expect(load).toHaveBeenCalledOnce();
  await act(async () => done("second"));
  expect(replace).toHaveBeenCalledWith("/articles/second", { scroll: false });
  replace.mockClear();
  fireEvent.click(screen.getByRole("button", { name: "Next article" }));
  fireEvent.click(screen.getByRole("button", { name: "Close preview" }));
  await act(async () => done("second"));
  expect(replace).not.toHaveBeenCalled();
});
it("keeps the original direct-route modal hidden when navigation returns to its slug", () => {
  render(
    <ArticleNavigationProvider overlay={{ slug: "first", entry: "intercepted", dismiss: "feed" }}>
      <ArticleModal direct slug="first">
        Original direct preview
      </ArticleModal>
      <ArticleModal slug="first">Routed preview</ArticleModal>
    </ArticleNavigationProvider>,
  );
  expect(screen.getAllByRole("dialog")).toHaveLength(1);
  expect(screen.queryByText("Original direct preview")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Close preview" }));
  expect(replace).toHaveBeenCalledWith("/", { scroll: true });
});

it("updates the intercepted article canonical and restores the retained feed on close", () => {
  const link = document.createElement("link");
  link.rel = "canonical";
  link.href = "https://devfeed.tech/topics/python";
  document.head.append(link);
  try {
    const view = render(
      <ArticleModal active slug="first" canonical="https://devfeed.tech/articles/first">
        Preview
      </ArticleModal>,
    );
    expect(document.head.querySelectorAll('link[rel="canonical"]')).toHaveLength(1);
    expect(link.href).toBe("https://devfeed.tech/articles/first");
    view.rerender(
      <ArticleModal active slug="first" canonical="https://devfeed.tech/articles/second">
        Preview
      </ArticleModal>,
    );
    expect(link.href).toBe("https://devfeed.tech/articles/second");
    view.unmount();
    expect(link.href).toBe("https://devfeed.tech/topics/python");
  } finally {
    link.remove();
  }
});

it("dismisses once after the exit animation, and never navigates after unmount", async () => {
  let finish!: () => void;
  const cancel = vi.fn();
  Object.defineProperty(Element.prototype, "animate", {
    configurable: true,
    writable: true,
    value: () => {},
  });
  vi.spyOn(Element.prototype, "animate").mockImplementation(
    () =>
      ({
        finished: new Promise<void>((resolve) => {
          finish = resolve;
        }),
        cancel,
      }) as unknown as Animation,
  );
  const view = render(
    <ArticleModal active slug="first">
      Preview
    </ArticleModal>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Close preview" }));
  fireEvent.click(screen.getByRole("button", { name: "Close preview" }));
  expect(back).not.toHaveBeenCalled();
  await act(async () => finish());
  expect(back).toHaveBeenCalledOnce();
  view.unmount();
  back.mockClear();
  const second = render(
    <ArticleModal active slug="first">
      Preview
    </ArticleModal>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Close preview" }));
  second.unmount();
  await act(async () => finish());
  expect(back).not.toHaveBeenCalled();
  expect(cancel).toHaveBeenCalled();
});

it("renders direct-entry content visibly in server HTML without opening intercepted previews", () => {
  const direct = renderToString(
    <ArticleModal active direct slug="first">
      Early summary
    </ArticleModal>,
  );
  expect(direct).toContain('open=""');
  expect(direct).toContain('data-direct-entry="true"');
  expect(direct).toContain("Early summary");
  const intercepted = renderToString(
    <ArticleModal active slug="first">
      Intercepted summary
    </ArticleModal>,
  );
  expect(intercepted).not.toContain('open=""');
});

it("upgrades the server-open direct dialog without an opacity entrance animation", () => {
  const animate = vi.fn(() => ({ finished: Promise.resolve(), cancel: vi.fn() }));
  Object.defineProperty(Element.prototype, "animate", { configurable: true, value: animate });
  const show = vi.spyOn(HTMLDialogElement.prototype, "showModal").mockImplementation(function (
    this: HTMLDialogElement,
  ) {
    expect(this.open).toBe(false);
    this.setAttribute("open", "");
  });
  render(
    <ArticleModal active direct slug="first">
      Early summary
    </ArticleModal>,
  );
  expect(show).toHaveBeenCalledOnce();
  expect(animate).not.toHaveBeenCalled();
  expect(document.querySelector("dialog[open]")).toBeTruthy();
});

it("keeps a parser-opened native dialog open throughout hydration", () => {
  const matches = Element.prototype.matches;
  vi.spyOn(HTMLDialogElement.prototype, "matches").mockImplementation(function (
    this: HTMLDialogElement,
    selector: string,
  ) {
    return selector === ":modal" ? this.open : matches.call(this, selector);
  });
  const show = vi.spyOn(HTMLDialogElement.prototype, "showModal");
  const close = vi.spyOn(HTMLDialogElement.prototype, "close");
  render(
    <ArticleModal active direct slug="first">
      Early summary
    </ArticleModal>,
  );
  expect(show).not.toHaveBeenCalled();
  expect(close).not.toHaveBeenCalled();
  expect(document.querySelector("dialog")?.dataset.hydrated).toBe("true");
});

function ReaderState() {
  return <output data-testid="reader-state">{JSON.stringify(useReaderContext())}</output>;
}

it("publishes only a loaded foreground article and restores the retained page on unmount", () => {
  function Page({ article, preview = true }: { article?: ArticleIdentity; preview?: boolean }) {
    return (
      <ReaderContextProvider>
        {preview && (
          <ArticleModal active slug="first" article={article}>
            Preview
          </ArticleModal>
        )}
        <ReaderSurface promptEligibility={readerPromptPolicies.browsing} />
        <ReaderState />
      </ReaderContextProvider>
    );
  }
  const state = () => JSON.parse(screen.getByTestId("reader-state").textContent ?? "null");
  const view = render(<Page />);
  expect(state()).toMatchObject({
    currentArticle: null,
    promptEligibility: readerPromptPolicies.quiet,
  });
  view.rerender(<Page article={{ id: "first-id", slug: "first" }} />);
  expect(state()).toMatchObject({
    currentArticle: { id: "first-id", slug: "first" },
    promptEligibility: readerPromptPolicies.quiet,
  });
  view.rerender(<Page article={{ id: "second-id", slug: "second" }} />);
  expect(state().currentArticle).toEqual({ id: "second-id", slug: "second" });
  view.rerender(<Page />);
  expect(state()).toMatchObject({
    currentArticle: null,
    promptEligibility: readerPromptPolicies.quiet,
  });
  view.rerender(<Page preview={false} />);
  expect(state()).toMatchObject({
    currentArticle: null,
    promptEligibility: readerPromptPolicies.browsing,
  });
});

it("keeps a hidden direct-entry article out of reader context while a routed preview is active", () => {
  render(
    <ReaderContextProvider>
      <ArticleNavigationProvider overlay={{ slug: "first", entry: "intercepted", dismiss: "feed" }}>
        <ArticleModal direct slug="first" article={{ id: "original-id", slug: "first" }}>
          Original direct preview
        </ArticleModal>
        <ArticleModal slug="first" article={{ id: "routed-id", slug: "first" }}>
          Routed preview
        </ArticleModal>
        <ReaderState />
      </ArticleNavigationProvider>
    </ReaderContextProvider>,
  );
  expect(screen.queryByText("Original direct preview")).toBeNull();
  expect(JSON.parse(screen.getByTestId("reader-state").textContent ?? "null")).toMatchObject({
    currentArticle: { id: "routed-id", slug: "first" },
    promptEligibility: readerPromptPolicies.quiet,
  });
});

it("selects one overlay from framework slots through direct, intercepted, Back and Forward entries", () => {
  function Page() {
    return (
      <ReaderContextProvider>
        <WebArticleNavigationProvider>
          <ArticleModal direct slug="first" article={{ id: "first-id", slug: "first" }}>
            Original direct preview
          </ArticleModal>
          <ArticleModal
            article={
              slots.modal.length ? { id: "routed-id", slug: slots.modal.at(-1)! } : undefined
            }
          >
            Routed preview
          </ArticleModal>
          <ReaderState />
        </WebArticleNavigationProvider>
      </ReaderContextProvider>
    );
  }
  slots.page = ["(reader)", "articles", "first"];
  const view = render(<Page />);
  expect(screen.getAllByRole("dialog")).toHaveLength(1);
  expect(screen.getByText("Original direct preview")).toBeTruthy();
  slots.modal = ["(__SLOT__)", "(.)articles", "second"];
  view.rerender(<Page />);
  expect(screen.getAllByRole("dialog")).toHaveLength(1);
  expect(screen.queryByText("Original direct preview")).toBeNull();
  expect(
    JSON.parse(screen.getByTestId("reader-state").textContent ?? "null").currentArticle,
  ).toEqual({
    id: "routed-id",
    slug: "second",
  });
  slots.modal = ["(__SLOT__)", "(.)articles", "first"];
  view.rerender(<Page />);
  expect(screen.getAllByRole("dialog")).toHaveLength(1);
  expect(screen.queryByText("Original direct preview")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Close preview" }));
  expect(replace).toHaveBeenCalledWith("/", { scroll: true });
  slots.modal = [];
  view.rerender(<Page />);
  expect(screen.getAllByRole("dialog")).toHaveLength(1);
  expect(screen.getByText("Original direct preview")).toBeTruthy();
  slots.modal = ["(__SLOT__)", "(.)articles", "second"];
  view.rerender(<Page />);
  expect(screen.getAllByRole("dialog")).toHaveLength(1);
  slots.page = ["bookmarks"];
  slots.modal = ["(__SLOT__)", "bookmarks"];
  view.rerender(<Page />);
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(document.body.style.overflow).toBe("");
});

it("uses the requested overlay slug for navigation before an article loads or when it is unavailable", () => {
  slots.page = ["topics", "python"];
  slots.modal = ["(__SLOT__)", "(.)articles", "second"];
  render(
    <ReaderContextProvider>
      <WebArticleNavigationProvider>
        <Sequence
          value={{
            slugs: ["first", "second", "third"],
            hasMore: false,
            loading: false,
            loadMore: async () => undefined,
          }}
        >
          <ArticleModal>
            <p>Article unavailable</p>
          </ArticleModal>
          <ReaderState />
        </Sequence>
      </WebArticleNavigationProvider>
    </ReaderContextProvider>,
  );
  expect(
    JSON.parse(screen.getByTestId("reader-state").textContent ?? "null").currentArticle,
  ).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Previous article" }));
  expect(replace).toHaveBeenLastCalledWith("/articles/first", { scroll: false });
  fireEvent.click(screen.getByRole("button", { name: "Next article" }));
  expect(replace).toHaveBeenLastCalledWith("/articles/third", { scroll: false });
  fireEvent.click(screen.getByRole("button", { name: "Close preview" }));
  expect(back).toHaveBeenCalledOnce();
});

it("does not activate an unregistered article overlay", () => {
  render(<ArticleModal slug="first">Retained article</ArticleModal>);
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(document.body.style.overflow).toBe("");
});

it("does not treat an article word inside another registered route as an article overlay", () => {
  slots.page = ["(reader)", "tags", "articles", "tutorials"];
  slots.modal = ["(__SLOT__)", "tags/articles/tutorials"];
  render(
    <WebArticleNavigationProvider>
      <ArticleModal direct>Retained direct article</ArticleModal>
      <ArticleModal>Retained intercepted article</ArticleModal>
    </WebArticleNavigationProvider>,
  );
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(document.body.style.overflow).toBe("");
});

it("invalidates a pending next-page move when the retained dialog receives another requested article", async () => {
  let finish!: (slug: string) => void;
  const loadMore = vi.fn(
    () =>
      new Promise<string>((resolve) => {
        finish = resolve;
      }),
  );
  const sequence = { slugs: ["first", "second"], hasMore: true, loading: false, loadMore };
  function Page({ slug }: { slug: string }) {
    return (
      <ArticleNavigationProvider overlay={{ slug, entry: "intercepted", dismiss: "history" }}>
        <Sequence value={sequence}>
          <ArticleModal slug={slug}>Requested article</ArticleModal>
        </Sequence>
      </ArticleNavigationProvider>
    );
  }
  const view = render(<Page slug="second" />);
  fireEvent.click(screen.getByRole("button", { name: "Next article" }));
  expect(loadMore).toHaveBeenCalledOnce();
  view.rerender(<Page slug="first" />);
  expect(screen.getByRole("button", { name: "Next article" }).hasAttribute("disabled")).toBe(false);
  await act(async () => finish("third"));
  expect(replace).not.toHaveBeenCalled();
});

it("cancels an unfinished dismissal when the retained dialog receives another requested article", async () => {
  vi.useFakeTimers();
  const animations: { finish: () => void; cancel: ReturnType<typeof vi.fn> }[] = [];
  Object.defineProperty(Element.prototype, "animate", {
    configurable: true,
    value: () => {
      let finish!: () => void;
      const finished = new Promise<void>((resolve) => {
        finish = resolve;
      });
      const cancel = vi.fn();
      animations.push({ finish, cancel });
      return { finished, cancel };
    },
  });
  const show = vi.spyOn(HTMLDialogElement.prototype, "showModal");
  const close = vi.spyOn(HTMLDialogElement.prototype, "close");
  try {
    const view = render(
      <ArticleModal active slug="first">
        First article
      </ArticleModal>,
    );
    const element = screen.getByRole("dialog");
    fireEvent.click(screen.getByRole("button", { name: "Close preview" }));
    const staleExit = animations.at(-1)!;
    expect(element.dataset.closing).toBe("true");
    view.rerender(
      <ArticleModal active slug="second">
        Second article
      </ArticleModal>,
    );
    expect(screen.getByRole("dialog")).toBe(element);
    expect(staleExit.cancel).toHaveBeenCalledOnce();
    expect(element.dataset.closing).toBeUndefined();
    expect(show).toHaveBeenCalledOnce();
    expect(close).not.toHaveBeenCalled();
    await act(async () => {
      staleExit.finish();
      await vi.advanceTimersByTimeAsync(200);
    });
    expect(back).not.toHaveBeenCalled();
    expect(document.body.style.overflow).toBe("hidden");
    expect(document.querySelector("dialog[open]")).toBe(element);
    fireEvent.click(screen.getByRole("button", { name: "Close preview" }));
    const currentExit = animations.at(-1)!;
    expect(currentExit).not.toBe(staleExit);
    await act(async () => currentExit.finish());
    expect(back).toHaveBeenCalledOnce();
    expect(document.querySelector("dialog[open]")).toBeNull();
    expect(document.body.style.overflow).toBe("");
  } finally {
    vi.useRealTimers();
  }
});

it("resets preview scroll for requested article changes rather than article loading", () => {
  const scroll = vi.fn();
  Object.defineProperty(HTMLElement.prototype, "scrollTo", { configurable: true, value: scroll });
  try {
    const view = render(
      <ArticleModal active slug="first">
        <div className="preview-scroll" />
      </ArticleModal>,
    );
    expect(scroll).toHaveBeenCalledOnce();
    view.rerender(
      <ArticleModal active slug="first" article={{ id: "first-id", slug: "first" }}>
        <div className="preview-scroll" />
      </ArticleModal>,
    );
    expect(scroll).toHaveBeenCalledOnce();
    view.rerender(
      <ArticleModal active slug="second">
        <div className="preview-scroll" />
      </ArticleModal>,
    );
    expect(scroll).toHaveBeenCalledTimes(2);
    expect(scroll).toHaveBeenLastCalledWith({ top: 0 });
  } finally {
    Reflect.deleteProperty(HTMLElement.prototype, "scrollTo");
  }
});
