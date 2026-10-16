// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MustReads } from "@/components/must-reads";
import { openMustReadsEvent } from "@/lib/reading-streak";
import { article } from "./fixtures";
const state = vi.hoisted(() => ({
  user: { user_id: "reader", csrf_token: "csrf" } as { user_id: string; csrf_token: string } | null,
  currentArticle: null as { id: string; slug: string } | null,
  promptEligibility: { signup: true, mustReads: true },
  loading: false,
  request: vi.fn(),
}));
vi.mock("@/components/user-account", () => ({ useUser: () => state }));
vi.mock("@/components/reader-context", () => ({ useReaderContext: () => state }));
vi.mock("@/lib/user", () => ({ userRequest: state.request }));
vi.mock("@/components/article-card", () => ({
  ArticleCard: ({ onOpen }: { onOpen?: () => void }) => (
    <article>
      <button onClick={onOpen}>Open pick</button>
    </article>
  ),
}));
vi.mock("@/components/article-engagement", () => ({
  EngagementProvider: ({ children }: { children: React.ReactNode }) => children,
  bookmarkChanged: "bookmark",
}));
beforeEach(() => {
  vi.useFakeTimers();
  state.user = { user_id: "reader", csrf_token: "csrf" };
  state.currentArticle = null;
  state.promptEligibility = { signup: true, mustReads: true };
  state.loading = false;
  state.request.mockReset();
  state.request.mockImplementation(async (path: string) =>
    path.endsWith("presentation")
      ? { claimed: true }
      : {
          date: "2026-10-03",
          items: [article],
          reasons: {},
          read_ids: [],
          presented: false,
          preparing: false,
        },
  );
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close = function () {
    this.removeAttribute("open");
  };
});
afterEach(() => {
  cleanup();
  document.querySelectorAll("[data-must-reads-blocker]").forEach((node) => node.remove());
  vi.restoreAllMocks();
  vi.useRealTimers();
  window.history.replaceState({}, "", "/");
});
it("defers presentation while reading an article, then claims once and permits reopening", async () => {
  state.currentArticle = { id: "test", slug: "test" };
  const view = render(<MustReads />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(3000);
  });
  expect(state.request.mock.calls.some(([path]) => path.endsWith("presentation"))).toBe(false);
  state.currentArticle = null;
  view.rerender(<MustReads />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(3000);
  });
  expect(screen.getByRole("dialog", { name: "Today’s Must Reads" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Close Must Reads" }));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(3000);
  });
  expect(screen.queryByRole("dialog")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Today’s Must Reads" }));
  expect(screen.getByRole("dialog", { name: "Today’s Must Reads" })).toBeTruthy();
  state.user = null;
  view.rerender(<MustReads />);
  expect(screen.queryByRole("dialog")).toBeNull();
});
it("keeps automatic presentation closed when another device claimed it", async () => {
  state.request.mockImplementation(async (path: string) =>
    path.endsWith("presentation")
      ? { claimed: false }
      : { date: "2026-10-03", items: [article], reasons: {}, read_ids: [], presented: false },
  );
  render(<MustReads />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(6000);
  });
  expect(screen.queryByRole("dialog")).toBeNull();
});
it.each(["article", "route", "dialog", "menu", "hidden"])(
  "keeps an in-flight claim pending while blocked by %s, even after a selection refresh",
  async (blocker) => {
    let resolveClaim!: (value: { claimed: boolean }) => void;
    let claimed = false;
    state.request.mockImplementation((path: string) =>
      path.endsWith("presentation")
        ? new Promise((resolve) => {
            resolveClaim = resolve;
          })
        : Promise.resolve({
            date: "2026-10-03",
            items: [article],
            reasons: {},
            read_ids: [],
            presented: claimed,
            preparing: false,
          }),
    );
    const view = render(<MustReads />);
    await act(async () => {});
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    const overlay = document.createElement("div");
    overlay.setAttribute("data-must-reads-blocker", "");
    if (blocker === "article") {
      state.currentArticle = { id: "test", slug: "test" };
      view.rerender(<MustReads />);
    } else if (blocker === "route") {
      state.promptEligibility.mustReads = false;
      view.rerender(<MustReads />);
    } else if (blocker === "hidden") {
      vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
    } else {
      overlay.setAttribute("role", blocker);
      if (blocker === "menu") overlay.setAttribute("data-state", "open");
      document.body.append(overlay);
    }
    await act(async () => {
      claimed = true;
      resolveClaim({ claimed: true });
    });
    expect(screen.queryByRole("dialog", { name: "Today’s Must Reads" })).toBeNull();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(6000);
    });
    if (blocker === "hidden") vi.restoreAllMocks();
    await act(async () => {
      window.dispatchEvent(new Event("focus"));
    });
    if (blocker !== "hidden") {
      expect(screen.queryByRole("dialog", { name: "Today’s Must Reads" })).toBeNull();
    }
    overlay.remove();
    state.currentArticle = null;
    state.promptEligibility.mustReads = true;
    view.rerender(<MustReads />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(screen.getByRole("dialog", { name: "Today’s Must Reads" })).toBeTruthy();
    expect(state.request.mock.calls.filter(([path]) => path.endsWith("presentation"))).toHaveLength(
      1,
    );
    fireEvent.click(screen.getByRole("button", { name: "Close Must Reads" }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(6000);
    });
    expect(screen.queryByRole("dialog", { name: "Today’s Must Reads" })).toBeNull();
  },
);
it("waits for an existing dialog to close before presenting", async () => {
  const existing = document.createElement("dialog");
  existing.setAttribute("open", "");
  document.body.append(existing);
  render(<MustReads />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(3000);
  });
  expect(state.request.mock.calls.some(([path]) => path.endsWith("presentation"))).toBe(false);
  existing.remove();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(3000);
  });
  expect(screen.getByRole("dialog", { name: "Today’s Must Reads" })).toBeTruthy();
});
it("never automatically presents an empty or preparing selection", async () => {
  state.request.mockResolvedValue({
    date: "2026-10-03",
    items: [],
    reasons: {},
    read_ids: [],
    presented: false,
    preparing: true,
  });
  render(<MustReads />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(6000);
  });
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(state.request.mock.calls.some(([path]) => path.endsWith("presentation"))).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "Today’s Must Reads" }));
  expect(screen.getByRole("status").textContent).toContain("being prepared");
});
it("refreshes the selection at the next local midnight", async () => {
  vi.setSystemTime(new Date(2026, 9, 3, 23, 59, 58));
  state.request.mockResolvedValue({
    date: "2026-10-03",
    items: [article],
    reasons: {},
    read_ids: [],
    presented: true,
    preparing: false,
  });
  render(<MustReads />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(state.request).toHaveBeenCalledTimes(1);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(3100);
  });
  expect(state.request).toHaveBeenCalledTimes(2);
});

it("opens today's picks from the streak action as a manual presentation", async () => {
  state.currentArticle = { id: "test", slug: "test" };
  state.request.mockImplementation(async (path: string) =>
    path.endsWith("presentation")
      ? { claimed: true }
      : {
          date: "2026-10-03",
          items: [article],
          reasons: {},
          read_ids: [],
          presented: true,
          preparing: false,
        },
  );
  render(<MustReads />);
  await act(async () => {});
  expect(screen.queryByRole("dialog")).toBeNull();
  await act(async () => {
    window.dispatchEvent(new Event(openMustReadsEvent));
  });
  expect(screen.getByRole("dialog", { name: "Today’s Must Reads" })).toBeTruthy();
  const presentations = state.request.mock.calls.filter(([path]) => path.endsWith("presentation"));
  expect(presentations).toHaveLength(1);
  expect(JSON.parse(presentations[0][1].body)).toMatchObject({
    date: "2026-10-03",
    automatic: false,
  });
});

it("removes the streak action listener on sign out", async () => {
  const view = render(<MustReads />);
  await act(async () => {});
  state.user = null;
  view.rerender(<MustReads />);
  const callsBefore = state.request.mock.calls.length;
  await act(async () => {
    window.dispatchEvent(new Event(openMustReadsEvent));
  });
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(state.request).toHaveBeenCalledTimes(callsBefore);
});

it("uses context eligibility independently of the current URL", async () => {
  state.promptEligibility.mustReads = false;
  window.history.replaceState({}, "", "/latest");
  const view = render(<MustReads />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(6000);
  });
  expect(state.request.mock.calls.some(([path]) => path.endsWith("presentation"))).toBe(false);

  state.promptEligibility.mustReads = true;
  window.history.replaceState({}, "", "/articles/not-the-current-article");
  view.rerender(<MustReads />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(3000);
  });
  expect(screen.getByRole("dialog", { name: "Today’s Must Reads" })).toBeTruthy();
});

it("keeps manual picks available while automatic prompts are disabled", async () => {
  state.promptEligibility.mustReads = false;
  const view = render(<MustReads />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(3000);
  });
  expect(screen.queryByRole("dialog")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Today’s Must Reads" }));
  await act(async () => {});
  expect(screen.getByRole("dialog", { name: "Today’s Must Reads" })).toBeTruthy();
  const presentation = state.request.mock.calls.find(([path]) => path.endsWith("presentation"));
  expect(JSON.parse(presentation![1].body)).toMatchObject({
    date: "2026-10-03",
    automatic: false,
  });
  view.rerender(<MustReads />);
  expect(screen.getByRole("dialog", { name: "Today’s Must Reads" })).toBeTruthy();
});

it("closes picks when the current article changes and permits reopening on that article", async () => {
  state.promptEligibility.mustReads = false;
  const view = render(<MustReads />);
  await act(async () => {});
  fireEvent.click(screen.getByRole("button", { name: "Today’s Must Reads" }));
  expect(screen.getByRole("dialog", { name: "Today’s Must Reads" })).toBeTruthy();

  state.currentArticle = { id: "selected-pick", slug: "selected-pick" };
  view.rerender(<MustReads />);
  expect(screen.queryByRole("dialog")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Today’s Must Reads" }));
  expect(screen.getByRole("dialog", { name: "Today’s Must Reads" })).toBeTruthy();
  state.currentArticle = { id: "selected-pick", slug: "selected-pick" };
  view.rerender(<MustReads />);
  expect(screen.getByRole("dialog", { name: "Today’s Must Reads" })).toBeTruthy();

  state.currentArticle = { id: "next-pick", slug: "next-pick" };
  view.rerender(<MustReads />);
  expect(screen.queryByRole("dialog")).toBeNull();
});

it("closes a selected pick even when it is the current article and permits manual reopening", async () => {
  state.currentArticle = { id: article.id, slug: article.slug };
  state.promptEligibility.mustReads = false;
  render(<MustReads />);
  await act(async () => {});
  fireEvent.click(screen.getByRole("button", { name: "Today’s Must Reads" }));
  expect(screen.getByRole("dialog", { name: "Today’s Must Reads" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Open pick" }));
  expect(screen.queryByRole("dialog")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Today’s Must Reads" }));
  expect(screen.getByRole("dialog", { name: "Today’s Must Reads" })).toBeTruthy();
});

it("retains a successful pending claim through same-account session revalidation", async () => {
  let resolveClaim!: (value: { claimed: boolean }) => void;
  let claimed = false;
  state.request.mockImplementation((path: string) =>
    path.endsWith("presentation")
      ? new Promise((resolve) => {
          resolveClaim = resolve;
        })
      : Promise.resolve({
          date: "2026-10-03",
          items: [article],
          reasons: {},
          read_ids: [],
          presented: claimed,
          preparing: false,
        }),
  );
  const view = render(<MustReads />);
  await act(async () => {});
  await act(async () => {
    await vi.advanceTimersByTimeAsync(3000);
  });
  const presentation = state.request.mock.calls.find(([path]) => path.endsWith("presentation"));
  state.currentArticle = { id: article.id, slug: article.slug };
  view.rerender(<MustReads />);
  await act(async () => {
    claimed = true;
    resolveClaim({ claimed: true });
  });
  expect(screen.queryByRole("dialog")).toBeNull();

  state.loading = true;
  state.currentArticle = null;
  view.rerender(<MustReads />);
  expect(
    (screen.getByRole("button", { name: "Today’s Must Reads" }) as HTMLButtonElement).disabled,
  ).toBe(true);
  expect(presentation![1].signal.aborted).toBe(false);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(3000);
  });
  expect(screen.queryByRole("dialog")).toBeNull();

  state.user = { user_id: "reader", csrf_token: "refreshed-csrf" };
  state.loading = false;
  view.rerender(<MustReads />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(3000);
  });
  expect(screen.getByRole("dialog", { name: "Today’s Must Reads" })).toBeTruthy();
  expect(state.request.mock.calls.filter(([path]) => path.endsWith("presentation"))).toHaveLength(
    1,
  );
});

it("keeps an automatic presentation pending while revalidation returns to a quiet foreground", async () => {
  let presented = false;
  state.request.mockImplementation(async (path: string) => {
    if (path.endsWith("presentation")) {
      presented = true;
      return { claimed: true };
    }
    return {
      date: "2026-10-03",
      items: [article],
      reasons: {},
      read_ids: [],
      presented,
      preparing: false,
    };
  });
  const view = render(<MustReads />);
  await act(async () => {});
  await act(async () => {
    await vi.advanceTimersByTimeAsync(3000);
  });
  expect(screen.getByRole("dialog", { name: "Today’s Must Reads" })).toBeTruthy();

  state.loading = true;
  state.promptEligibility.mustReads = false;
  state.currentArticle = null;
  view.rerender(<MustReads />);
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(document.body.style.overflow).toBe("");
  state.loading = false;
  view.rerender(<MustReads />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(6000);
  });
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(document.body.style.overflow).toBe("");

  state.promptEligibility.mustReads = true;
  view.rerender(<MustReads />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(3000);
  });
  expect(screen.getByRole("dialog", { name: "Today’s Must Reads" })).toBeTruthy();
  expect(state.request.mock.calls.filter(([path]) => path.endsWith("presentation"))).toHaveLength(
    1,
  );
  fireEvent.click(screen.getByRole("button", { name: "Close Must Reads" }));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(3000);
  });
  expect(screen.queryByRole("dialog")).toBeNull();
});

it("allows manual presentation after revalidation ends on a quiet foreground", async () => {
  state.loading = true;
  state.promptEligibility.mustReads = false;
  const view = render(<MustReads />);
  state.loading = false;
  view.rerender(<MustReads />);
  await act(async () => {});
  fireEvent.click(screen.getByRole("button", { name: "Today’s Must Reads" }));
  await act(async () => {});
  expect(screen.getByRole("dialog", { name: "Today’s Must Reads" })).toBeTruthy();
  const presentation = state.request.mock.calls.find(([path]) => path.endsWith("presentation"));
  expect(JSON.parse(presentation![1].body)).toMatchObject({ automatic: false });
});

it.each(["signout", "account change"])(
  "clears an old pending claim on %s during revalidation",
  async (change) => {
    let resolveClaim!: (value: { claimed: boolean }) => void;
    let presented = false;
    state.request.mockImplementation((path: string) =>
      path.endsWith("presentation")
        ? new Promise((resolve) => {
            resolveClaim = resolve;
          })
        : Promise.resolve({
            date: "2026-10-03",
            items: [article],
            reasons: {},
            read_ids: [],
            presented,
            preparing: false,
          }),
    );
    const view = render(<MustReads />);
    await act(async () => {});
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    const presentation = state.request.mock.calls.find(([path]) => path.endsWith("presentation"));
    state.loading = true;
    view.rerender(<MustReads />);
    state.user =
      change === "signout" ? null : { user_id: "other-reader", csrf_token: "other-csrf" };
    view.rerender(<MustReads />);
    expect(presentation![1].signal.aborted).toBe(true);
    await act(async () => {
      presented = true;
      resolveClaim({ claimed: true });
    });
    state.loading = false;
    state.user ??= { user_id: "reader", csrf_token: "new-session-csrf" };
    view.rerender(<MustReads />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(state.request.mock.calls.filter(([path]) => path.endsWith("presentation"))).toHaveLength(
      1,
    );
  },
);

it("suspends fetching and manual presentation while the retained account is revalidated", async () => {
  state.loading = true;
  const view = render(<MustReads />);
  const trigger = screen.getByRole("button", { name: "Today’s Must Reads" });
  expect((trigger as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(trigger);
  await act(async () => {
    window.dispatchEvent(new Event(openMustReadsEvent));
    window.dispatchEvent(new Event("focus"));
    await vi.advanceTimersByTimeAsync(3000);
  });
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(state.request).not.toHaveBeenCalled();

  state.loading = false;
  view.rerender(<MustReads />);
  await act(async () => {});
  fireEvent.click(screen.getByRole("button", { name: "Today’s Must Reads" }));
  await act(async () => {});
  expect(screen.getByRole("dialog", { name: "Today’s Must Reads" })).toBeTruthy();
  const calls = state.request.mock.calls.length;
  state.loading = true;
  view.rerender(<MustReads />);
  await act(async () => {
    window.dispatchEvent(new Event(openMustReadsEvent));
    await vi.advanceTimersByTimeAsync(3000);
  });
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(document.body.style.overflow).toBe("");
  expect(state.request).toHaveBeenCalledTimes(calls);
});

it("pauses remaining bookmark writes when session revalidation begins", async () => {
  state.promptEligibility.mustReads = false;
  let resolveBookmark!: (value: { article_id: string; bookmarked: boolean }) => void;
  state.request.mockImplementation((path: string) => {
    if (path.endsWith("presentation")) return Promise.resolve({ claimed: true });
    if (path.endsWith("bookmark"))
      return new Promise((resolve) => {
        resolveBookmark = resolve;
      });
    return Promise.resolve({
      date: "2026-10-03",
      items: [article, { ...article, id: "second-pick", slug: "second-pick" }],
      reasons: {},
      read_ids: [],
      presented: false,
      preparing: false,
    });
  });
  const view = render(<MustReads />);
  await act(async () => {});
  fireEvent.click(screen.getByRole("button", { name: "Today’s Must Reads" }));
  await act(async () => {});
  fireEvent.click(screen.getByRole("button", { name: "Save unread picks" }));
  state.loading = true;
  view.rerender(<MustReads />);
  await act(async () => {
    resolveBookmark({ article_id: article.id, bookmarked: true });
  });
  expect(state.request.mock.calls.filter(([path]) => path.endsWith("bookmark"))).toHaveLength(1);
});
