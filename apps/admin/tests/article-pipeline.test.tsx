// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { toast } from "sonner";
import { renderAdmin } from "./render-admin";
import { RefreshSettings, saveRefresh } from "./refresh-settings";
import { ArticlePipeline } from "@/components/organisms/article-pipeline";
import { ArticleReadiness } from "@/components/organisms/article-readiness";
import { ResourceWorkflow } from "@/components/organisms/resource-workflow";
import { ResourceList } from "@/components/organisms/resource-list";
import { ResourceDetail } from "@/components/organisms/resource-detail";
import {
  advanceArticlePipeline,
  getArticlePipeline,
  getArticlePipelineColumn,
  getArticlePipelineDetail,
  getArticlePipelineSourceArticles,
  retryArticlePipeline,
  pipelineQuery,
  pipelineLabels,
  pipelineStages,
  type PipelineArticle,
  type PipelineBoard,
  type PipelineDetail,
} from "@/lib/article-pipeline";
import { getRecord, listRecords } from "@/lib/resource-api";
import { adminArticlePipelineColumn, adminArticleReview } from "@/lib/api/generated/admin";
import { ApiError } from "@/lib/api/client";

const navigation = vi.hoisted(() => ({ query: "view=pipeline" }));
const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }));
vi.mock("next/navigation", () => ({
  useRouter: () => router,
  useSearchParams: () => new URLSearchParams(navigation.query),
  usePathname: () => "/content/articles",
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() } }));
vi.mock("@/lib/article-pipeline", async (original) => ({
  ...(await original<typeof import("@/lib/article-pipeline")>()),
  getArticlePipeline: vi.fn(),
  getArticlePipelineColumn: vi.fn(),
  getArticlePipelineDetail: vi.fn(),
  getArticlePipelineSourceArticles: vi.fn(),
  advanceArticlePipeline: vi.fn(),
  retryArticlePipeline: vi.fn(),
}));
vi.mock("@/lib/resource-api", async (original) => ({
  ...(await original<typeof import("@/lib/resource-api")>()),
  getRecord: vi.fn(),
  listRecords: vi.fn(),
}));
vi.mock("@/lib/api/generated/admin", async (original) => ({
  ...(await original<typeof import("@/lib/api/generated/admin")>()),
  adminArticleReview: vi.fn(),
  adminArticlePipelineColumn: vi.fn(),
}));

const card: PipelineArticle = {
  id: "article-one",
  title: "Build reliable structured output clients",
  source_name: "Engineering Journal",
  source_id: "source-one",
  discovered_at: "2026-10-19T08:00:00Z",
  stage: "images",
  state: "running",
  blockers: ["images_processing"],
  next_check_at: null,
  editorial_revision: 7,
  jobs: {
    enrichment: {
      id: "text-job",
      status: "succeeded",
      outcome: "enriched",
      available_at: "2026-10-19T08:00:00Z",
      finished_at: "2026-10-19T08:01:00Z",
      retryable: false,
    },
    analysis: {
      id: "ai-job",
      status: "succeeded",
      outcome: "applied",
      available_at: "2026-10-19T08:01:00Z",
      finished_at: "2026-10-19T08:02:00Z",
      retryable: false,
    },
    images: {
      id: "image-job",
      status: "running",
      outcome: null,
      available_at: "2026-10-19T08:02:00Z",
      finished_at: null,
      retryable: false,
    },
  },
};
function board(item = card): PipelineBoard {
  return {
    as_of: "2026-10-19T08:03:00Z",
    terminal_since: "2026-10-12T08:03:00Z",
    counts: Object.fromEntries(
      pipelineStages.map((stage) => [stage, stage === item.stage ? 1 : 0]),
    ) as PipelineBoard["counts"],
    columns: pipelineStages.map((stage) => ({
      stage,
      total: stage === item.stage ? 1 : 0,
      items: stage === item.stage ? [item] : [],
      next_cursor: null,
    })),
  };
}
function groupedBoard(stage: PipelineArticle["stage"] = "images"): PipelineBoard {
  const result = board({ ...card, stage });
  const column = result.columns.find((column) => column.stage === stage)!;
  column.items = [];
  column.total = 31;
  column.source_groups = [
    {
      source_id: card.source_id,
      source_name: card.source_name,
      total: 31,
      state_counts: { running: 1, waiting: 29, blocked: 1 },
    },
  ];
  result.counts[stage] = 31;
  return result;
}
const detail: PipelineDetail = {
  article_id: card.id,
  editorial_revision: 7,
  stage: "images",
  state: "running",
  checks: [
    {
      code: "enrichment",
      label: "Article text",
      status: "passed",
      detail: "Source text is available.",
    },
    {
      code: "analysis",
      label: "AI analysis",
      status: "passed",
      detail: "Current source evidence analyzed.",
    },
    {
      code: "images",
      label: "Article images",
      status: "pending",
      detail: "An image lookup is still running.",
    },
  ],
  blockers: ["images_processing"],
  can_publish: false,
  jobs: card.jobs,
  allowed_actions: [],
  advance_label: null,
};
beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  navigation.query = "view=pipeline";
  vi.mocked(getArticlePipeline).mockResolvedValue(board());
  vi.mocked(getArticlePipelineDetail).mockResolvedValue(detail);
  vi.mocked(getArticlePipelineSourceArticles).mockResolvedValue({
    stage: "images",
    total: 1,
    items: [card],
    next_cursor: null,
  });
  vi.mocked(advanceArticlePipeline).mockResolvedValue(detail);
  vi.mocked(retryArticlePipeline).mockResolvedValue(detail);
  vi.mocked(listRecords).mockResolvedValue({ items: [], total: 0, limit: 25, offset: 0 });
  vi.mocked(getRecord).mockResolvedValue({
    id: card.id,
    title: card.title,
    editorial_revision: 7,
    publication_status: "unpublished",
    review_status: "approved",
    publication_blockers: [],
  });
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

function dragTransfer() {
  const values = new Map<string, string>();
  return {
    setData: (type: string, value: string) => values.set(type, value),
    getData: (type: string) => values.get(type) ?? "",
    get types() {
      return [...values.keys()];
    },
    effectAllowed: "all",
    dropEffect: "none",
    setDragImage: vi.fn(),
  };
}

it("forwards only bounded pipeline filters to the API", () => {
  expect(
    pipelineQuery(
      "q=Python&source_id=source-one&needs_attention=true&offset=500&limit=100&sort=title&view=pipeline",
    ).toString(),
  ).toBe("limit=20&q=Python&source_id=source-one&needs_attention=true");
});

it("keeps Articles as the default and selects source grouping through the URL", async () => {
  renderAdmin(<ArticlePipeline query="view=pipeline&stage=images" />);
  await screen.findByRole("link", { name: card.title });
  expect(screen.getByRole("combobox", { name: "Group cards" }).textContent).toContain("Articles");
  expect(getArticlePipelineSourceArticles).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("combobox", { name: "Group cards" }));
  fireEvent.click(screen.getByRole("option", { name: /^Source$/ }));
  expect(router.push).toHaveBeenCalledWith(expect.stringContaining("group_by=source"), {
    scroll: false,
  });
});

it("loads bounded source articles without grouping while retaining the article filters", async () => {
  const actual =
    await vi.importActual<typeof import("@/lib/article-pipeline")>("@/lib/article-pipeline");
  vi.mocked(adminArticlePipelineColumn).mockResolvedValue({
    stage: "images",
    total: 0,
    items: [],
    next_cursor: null,
  });
  const signal = new AbortController().signal;
  await actual.getArticlePipelineSourceArticles(
    "images",
    "view=pipeline&group_by=source&q=Python&source_id=selected&needs_attention=true&limit=100",
    null,
    20,
    signal,
  );
  expect(adminArticlePipelineColumn).toHaveBeenCalledWith(
    "images",
    {
      limit: 20,
      q: "Python",
      source_id: "selected",
      needs_attention: true,
      source_group: "unknown",
      cursor: 20,
    },
    { signal },
  );
});

it("renders collapsed source summaries using exact server article totals and state counts", async () => {
  const initial = groupedBoard();
  vi.mocked(getArticlePipeline).mockResolvedValue(initial);
  renderAdmin(<ArticlePipeline query="view=pipeline&group_by=source&stage=images" />);
  const group = await screen.findByRole("group", { name: "Engineering Journal source group" });
  expect(
    within(group)
      .getByRole("button", { name: "Expand Engineering Journal articles" })
      .getAttribute("aria-expanded"),
  ).toBe("false");
  expect(within(group).getByLabelText("31 articles from Engineering Journal")).toBeDefined();
  for (const state of ["1 Running", "29 Waiting", "1 Blocked"])
    expect(within(group).getByLabelText(state)).toBeDefined();
  expect(group.getAttribute("draggable")).not.toBe("true");
  expect(within(group).queryByRole("button", { name: /Move back/ })).toBeNull();
  expect(screen.queryByRole("link", { name: card.title })).toBeNull();
  expect(getArticlePipelineSourceArticles).not.toHaveBeenCalled();
  expect(screen.getByLabelText("31 articles")).toBeDefined();
});

it("opens unknown-source articles with the explicit unknown group rather than an unfiltered page", async () => {
  const initial = groupedBoard();
  initial.columns.find((column) => column.stage === "images")!.source_groups![0] = {
    source_id: null,
    source_name: null,
    total: 1,
    state_counts: { waiting: 1 },
  };
  initial.columns.find((column) => column.stage === "images")!.total = 1;
  initial.counts.images = 1;
  vi.mocked(getArticlePipeline).mockResolvedValue(initial);
  const query = "view=pipeline&group_by=source&stage=images";
  renderAdmin(<ArticlePipeline query={query} />);
  fireEvent.click(await screen.findByRole("button", { name: "Expand Unknown source articles" }));
  await screen.findByRole("link", { name: card.title });
  expect(getArticlePipelineSourceArticles).toHaveBeenCalledWith(
    "images",
    query,
    null,
    0,
    expect.any(AbortSignal),
  );
});

it("pages source groups without duplicate summaries and promotes a newly running group", async () => {
  const initial = groupedBoard();
  const images = initial.columns.find((column) => column.stage === "images")!;
  images.total = 4;
  initial.counts.images = 4;
  images.source_groups = [
    { source_id: "first", source_name: "Waiting Journal", total: 2, state_counts: { waiting: 2 } },
    { source_id: "later", source_name: "Later Journal", total: 2, state_counts: { waiting: 2 } },
  ];
  images.next_cursor = 20;
  vi.mocked(getArticlePipeline).mockResolvedValue(initial);
  vi.mocked(getArticlePipelineColumn).mockResolvedValue({
    stage: "images",
    total: 4,
    items: [],
    source_groups: [
      {
        source_id: "later",
        source_name: "Later Journal",
        total: 2,
        state_counts: { running: 1, waiting: 1 },
      },
    ],
    next_cursor: null,
  });
  const query = "view=pipeline&group_by=source&stage=images";
  renderAdmin(<ArticlePipeline query={query} />);
  fireEvent.click(await screen.findByRole("button", { name: "Load more images sources" }));
  await screen.findByLabelText("1 Running");
  const lane = screen.getByRole("heading", { name: "Images", level: 2 }).closest("section")!;
  expect(
    within(lane)
      .getAllByRole("group")
      .map((group) => group.getAttribute("data-pipeline-source-id")),
  ).toEqual(["later", "first"]);
  expect(screen.getAllByRole("button", { name: "Expand Later Journal articles" })).toHaveLength(1);
  expect(getArticlePipelineColumn).toHaveBeenCalledWith(
    "images",
    query,
    20,
    expect.any(AbortSignal),
  );
  expect(getArticlePipelineSourceArticles).not.toHaveBeenCalled();
});

it("keeps a source open across polling while refreshing its bounded first page", async () => {
  vi.useFakeTimers();
  const initial = groupedBoard();
  const refreshed = groupedBoard();
  refreshed.as_of = "2026-10-19T08:03:05Z";
  for (const snapshot of [initial, refreshed]) {
    const column = snapshot.columns.find((column) => column.stage === "images")!;
    column.total = 21;
    column.source_groups![0].total = 21;
    column.source_groups![0].state_counts = { running: 21 };
    snapshot.counts.images = 21;
  }
  vi.mocked(getArticlePipeline).mockResolvedValueOnce(initial).mockResolvedValue(refreshed);
  const firstPage = Array.from({ length: 20 }, (_, index) => ({
    ...card,
    id: `article-${index}`,
    title: `Grouped article ${index}`,
  }));
  const extra = { ...card, id: "article-20", title: "Grouped article 20" };
  vi.mocked(getArticlePipelineSourceArticles)
    .mockResolvedValueOnce({ stage: "images", total: 21, items: firstPage, next_cursor: 20 })
    .mockResolvedValueOnce({ stage: "images", total: 21, items: [extra], next_cursor: null })
    .mockResolvedValue({ stage: "images", total: 21, items: firstPage, next_cursor: 20 });
  await act(async () => {
    renderAdmin(
      <RefreshSettings>
        <ArticlePipeline query="view=pipeline&group_by=source&stage=images" />
      </RefreshSettings>,
    );
  });
  await saveRefresh(0);
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Expand Engineering Journal articles" }));
  });
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Load more Engineering Journal articles" }));
  });
  expect(screen.getByRole("link", { name: extra.title })).toBeDefined();
  await act(() => vi.advanceTimersByTimeAsync(5000));
  expect(
    screen
      .getByRole("button", { name: "Collapse Engineering Journal articles" })
      .getAttribute("aria-expanded"),
  ).toBe("true");
  expect(screen.queryByRole("link", { name: extra.title })).toBeNull();
  expect(screen.getAllByRole("link", { name: /^Grouped article/ })).toHaveLength(20);
  expect(vi.mocked(getArticlePipelineSourceArticles).mock.calls.map((call) => call[3])).toEqual([
    0, 20, 0,
  ]);
  expect(
    screen.getByRole("button", { name: "Load more Engineering Journal articles" }),
  ).toBeDefined();
});

it("keeps individual retry actions when a source is expanded across column collapse", async () => {
  const user = userEvent.setup();
  const initial = groupedBoard("ready");
  initial.columns.find((column) => column.stage === "ready")!.total = 1;
  initial.columns.find((column) => column.stage === "ready")!.source_groups![0].total = 1;
  initial.columns.find((column) => column.stage === "ready")!.source_groups![0].state_counts = {
    ready: 1,
  };
  initial.counts.ready = 1;
  vi.mocked(getArticlePipeline).mockResolvedValue(initial);
  vi.mocked(getArticlePipelineSourceArticles).mockResolvedValue({
    stage: "ready",
    total: 1,
    items: [{ ...card, stage: "ready", state: "ready", retry_targets: ["analysis"] }],
    next_cursor: null,
  });
  renderAdmin(<ArticlePipeline query="view=pipeline&group_by=source&stage=ready" />);
  await user.click(
    await screen.findByRole("button", { name: "Expand Engineering Journal articles" }),
  );
  await screen.findByRole("link", { name: card.title });
  fireEvent.doubleClick(screen.getByRole("heading", { name: "Ready", level: 2 }));
  expect(screen.getByRole("button", { name: "Expand Ready column" })).toBeDefined();
  fireEvent.click(screen.getByRole("button", { name: "Expand Ready column" }));
  expect(
    screen.getByRole("button", { name: "Collapse Engineering Journal articles" }),
  ).toBeDefined();
  await user.click(screen.getByRole("button", { name: `Move back ${card.title}` }));
  await user.click(screen.getByRole("menuitem", { name: "Retry AI analysis" }));
  expect(retryArticlePipeline).toHaveBeenCalledWith(card.id, "analysis", 7, "test-csrf", "ai-job");
});

it("withdraws a failed source-group refresh until its current page recovers", async () => {
  vi.useFakeTimers();
  vi.mocked(getArticlePipeline).mockResolvedValue(groupedBoard("ready"));
  const ready = {
    ...card,
    stage: "ready" as const,
    state: "ready",
    retry_targets: ["analysis" as const],
  };
  const page = { stage: "ready" as const, total: 1, items: [ready], next_cursor: null };
  vi.mocked(getArticlePipelineSourceArticles)
    .mockResolvedValueOnce(page)
    .mockRejectedValueOnce(
      new ApiError(503, "Analytics are being prepared.", {}, "analytics_preparing"),
    )
    .mockResolvedValue(page);
  await act(async () => {
    renderAdmin(
      <RefreshSettings>
        <ArticlePipeline query="view=pipeline&group_by=source&stage=ready" />
      </RefreshSettings>,
    );
  });
  await saveRefresh(0);
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Expand Engineering Journal articles" }));
  });
  expect(screen.getByRole("link", { name: card.title })).toBeTruthy();
  expect(screen.getByRole("button", { name: `Move back ${card.title}` })).toBeTruthy();
  await act(() => vi.advanceTimersByTimeAsync(5000));
  expect(screen.queryByRole("link", { name: card.title })).toBeNull();
  expect(screen.queryByRole("button", { name: `Move back ${card.title}` })).toBeNull();
  expect(screen.getByRole("alert").textContent).toContain("Analytics are being prepared.");
  await act(() => vi.advanceTimersByTimeAsync(5000));
  expect(screen.getByRole("link", { name: card.title })).toBeTruthy();
  expect(screen.queryByRole("alert")).toBeNull();
  expect(getArticlePipelineSourceArticles).toHaveBeenCalledTimes(3);
});

it("finishes a slow source request across multiple board polls without restarting it", async () => {
  vi.useFakeTimers();
  vi.mocked(getArticlePipeline).mockImplementation(async () => groupedBoard());
  let finish!: (value: Awaited<ReturnType<typeof getArticlePipelineSourceArticles>>) => void;
  vi.mocked(getArticlePipelineSourceArticles).mockImplementationOnce(
    () => new Promise((resolve) => (finish = resolve)),
  );
  await act(async () => {
    renderAdmin(
      <RefreshSettings>
        <ArticlePipeline query="view=pipeline&group_by=source&stage=images" />
      </RefreshSettings>,
    );
  });
  await saveRefresh(0);
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Expand Engineering Journal articles" }));
  });
  const signal = vi.mocked(getArticlePipelineSourceArticles).mock.calls[0][4];
  await act(() => vi.advanceTimersByTimeAsync(10000));
  expect(getArticlePipeline).toHaveBeenCalledTimes(3);
  expect(getArticlePipelineSourceArticles).toHaveBeenCalledTimes(1);
  expect(signal.aborted).toBe(false);
  await act(async () => {
    finish({ stage: "images", total: 1, items: [card], next_cursor: null });
  });
  expect(screen.getByRole("link", { name: card.title })).toBeDefined();
  expect(
    screen
      .getByRole("button", { name: "Collapse Engineering Journal articles" })
      .getAttribute("aria-expanded"),
  ).toBe("true");
});

it("fences an old source page while its refreshed first page is still pending", async () => {
  vi.useFakeTimers();
  vi.mocked(getArticlePipeline).mockImplementation(async () => groupedBoard());
  const firstPage = Array.from({ length: 20 }, (_, index) => ({
    ...card,
    id: `source-article-${index}`,
    title: `Source article ${index}`,
  }));
  const extra = { ...card, id: "late-page-two", title: "Stale source page two" };
  let finishMore!: (value: Awaited<ReturnType<typeof getArticlePipelineSourceArticles>>) => void;
  let finishRefresh!: (value: Awaited<ReturnType<typeof getArticlePipelineSourceArticles>>) => void;
  vi.mocked(getArticlePipelineSourceArticles)
    .mockResolvedValueOnce({ stage: "images", total: 21, items: firstPage, next_cursor: 20 })
    .mockImplementationOnce(() => new Promise((resolve) => (finishMore = resolve)))
    .mockImplementationOnce(() => new Promise((resolve) => (finishRefresh = resolve)));
  await act(async () => {
    renderAdmin(
      <RefreshSettings>
        <ArticlePipeline query="view=pipeline&group_by=source&stage=images" />
      </RefreshSettings>,
    );
  });
  await saveRefresh(0);
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Expand Engineering Journal articles" }));
  });
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Load more Engineering Journal articles" }));
  });
  const oldPageSignal = vi.mocked(getArticlePipelineSourceArticles).mock.calls[1][4];
  await act(() => vi.advanceTimersByTimeAsync(5000));
  expect(oldPageSignal.aborted).toBe(true);
  expect(getArticlePipelineSourceArticles).toHaveBeenCalledTimes(3);
  await act(async () => {
    finishMore({ stage: "images", total: 21, items: [extra], next_cursor: null });
  });
  expect(screen.queryByRole("link", { name: extra.title })).toBeNull();
  expect(
    (
      screen.getByRole("button", {
        name: "Load more Engineering Journal articles",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
  await act(async () => {
    finishRefresh({
      stage: "images",
      total: 21,
      items: firstPage.map((item) => ({ ...item, title: `Refreshed ${item.title}` })),
      next_cursor: 20,
    });
  });
  expect(screen.getAllByRole("link", { name: /^Refreshed Source article/ })).toHaveLength(20);
  expect(screen.queryByRole("link", { name: extra.title })).toBeNull();
  expect(
    (
      screen.getByRole("button", {
        name: "Load more Engineering Journal articles",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(false);
  expect(
    screen.getByRole("button", { name: "Collapse Engineering Journal articles" }),
  ).toBeDefined();
});

it("keeps the named board out of the tab order while supporting keyboard retries", async () => {
  const user = userEvent.setup();
  vi.mocked(getArticlePipeline).mockResolvedValue(
    board({ ...card, stage: "ready", state: "ready", retry_targets: ["analysis"] }),
  );
  renderAdmin(<ArticlePipeline query="view=pipeline&stage=ready" />);
  const region = await screen.findByRole("region", { name: "Article publication pipeline" });
  expect(region.tabIndex).toBe(-1);
  const articleLink = within(region).getByRole("link", { name: card.title });
  articleLink.focus();
  await user.tab();
  expect(document.activeElement).toBe(
    within(region).getByRole("button", { name: `Move back ${card.title}` }),
  );
  await user.keyboard("{Enter}");
  const retry = await screen.findByRole("menuitem", { name: "Retry AI analysis" });
  await waitFor(() => expect(document.activeElement).toBe(retry));
  await user.keyboard("{Enter}");
  expect(retryArticlePipeline).toHaveBeenCalledExactlyOnceWith(
    card.id,
    "analysis",
    7,
    "test-csrf",
    "ai-job",
  );
});

it("requests a retry with the target job baseline and keeps the old card until the new snapshot", async () => {
  const user = userEvent.setup();
  const ready = {
    ...card,
    stage: "ready" as const,
    state: "ready",
    retry_targets: ["analysis" as const],
  };
  let finishPost!: (value: PipelineDetail) => void;
  let finishGet!: (value: PipelineBoard) => void;
  vi.mocked(getArticlePipeline)
    .mockResolvedValueOnce(board(ready))
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finishGet = resolve;
        }),
    );
  vi.mocked(retryArticlePipeline).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finishPost = resolve;
      }),
  );
  renderAdmin(<ArticlePipeline query="view=pipeline&stage=ready" />);
  await user.click(await screen.findByRole("button", { name: `Move back ${card.title}` }));
  expect(screen.queryByRole("menuitem", { name: "Retry enrichment" })).toBeNull();
  await user.click(screen.getByRole("menuitem", { name: "Retry AI analysis" }));
  expect(retryArticlePipeline).toHaveBeenCalledWith(card.id, "analysis", 7, "test-csrf", "ai-job");
  const original = screen.getByRole("link", { name: card.title });
  expect(original.closest("article")?.getAttribute("data-pipeline-stage")).toBe("ready");
  await act(async () => finishPost({ ...detail, stage: "analysis" }));
  await waitFor(() => expect(getArticlePipeline).toHaveBeenCalledTimes(2));
  expect(screen.getByRole("link", { name: card.title })).toBe(original);
  expect(original.closest("article")?.getAttribute("data-pipeline-stage")).toBe("ready");
  expect(screen.queryByText("Loading…")).toBeNull();
  await act(async () =>
    finishGet(board({ ...ready, stage: "analysis", state: "waiting", retry_targets: [] })),
  );
  expect(screen.getAllByRole("link", { name: card.title })).toHaveLength(1);
  expect(
    screen
      .getByRole("link", { name: card.title })
      .closest("article")
      ?.getAttribute("data-pipeline-stage"),
  ).toBe("analysis");
});

it("retries through a real card drag onto an authorized earlier column and ignores duplicate busy drops", async () => {
  const ready = {
    ...card,
    stage: "ready" as const,
    state: "ready",
    retry_targets: ["enrichment" as const],
  };
  vi.mocked(getArticlePipeline).mockResolvedValue(board(ready));
  let finish!: (value: PipelineDetail) => void;
  vi.mocked(retryArticlePipeline).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  renderAdmin(<ArticlePipeline query="view=pipeline&stage=ready" />);
  const item = (await screen.findByRole("link", { name: card.title })).closest("article")!;
  expect(item.getAttribute("draggable")).toBe("true");
  const target = screen.getByRole("heading", { name: "Enrichment", level: 2 }).closest("section")!;
  const transfer = dragTransfer();
  fireEvent.dragStart(item, { dataTransfer: transfer });
  fireEvent.dragOver(target, { dataTransfer: transfer });
  fireEvent.drop(target, { dataTransfer: transfer });
  await waitFor(() =>
    expect(retryArticlePipeline).toHaveBeenCalledWith(
      card.id,
      "enrichment",
      7,
      "test-csrf",
      "text-job",
    ),
  );
  fireEvent.dragStart(item, { dataTransfer: transfer });
  fireEvent.dragOver(target, { dataTransfer: transfer });
  fireEvent.drop(target, { dataTransfer: transfer });
  expect(retryArticlePipeline).toHaveBeenCalledTimes(1);
  await act(async () => finish(detail));
});

it("ignores ineligible columns and external transfer IDs without a board dragstart", async () => {
  const ready = {
    ...card,
    stage: "ready" as const,
    state: "ready",
    retry_targets: ["images" as const],
  };
  vi.mocked(getArticlePipeline).mockResolvedValue(board(ready));
  renderAdmin(<ArticlePipeline query="view=pipeline&stage=ready" />);
  const item = (await screen.findByRole("link", { name: card.title })).closest("article")!;
  const transfer = dragTransfer();
  transfer.setData("application/x-devfeed-article-pipeline", card.id);
  transfer.setData("text/plain", card.id);
  const images = screen.getByRole("heading", { name: "Images", level: 2 }).closest("section")!;
  fireEvent.dragOver(images, { dataTransfer: transfer });
  fireEvent.drop(images, { dataTransfer: transfer });
  expect(retryArticlePipeline).not.toHaveBeenCalled();
  fireEvent.dragStart(item, { dataTransfer: transfer });
  const review = screen.getByRole("heading", { name: "Review", level: 2 }).closest("section")!;
  fireEvent.dragOver(review, { dataTransfer: transfer });
  fireEvent.drop(review, { dataTransfer: transfer });
  fireEvent.dragEnd(item, { dataTransfer: transfer });
  expect(retryArticlePipeline).not.toHaveBeenCalled();
});

it("uses a null job baseline for an allowed target without a previous run", async () => {
  const user = userEvent.setup();
  vi.mocked(getArticlePipeline).mockResolvedValue(
    board({
      ...card,
      stage: "ready",
      state: "ready",
      jobs: { ...card.jobs, enrichment: null },
      retry_targets: ["enrichment"],
    }),
  );
  renderAdmin(<ArticlePipeline query="view=pipeline&stage=ready" />);
  await user.click(await screen.findByRole("button", { name: `Move back ${card.title}` }));
  await user.click(screen.getByRole("menuitem", { name: "Retry enrichment" }));
  expect(retryArticlePipeline).toHaveBeenCalledWith(card.id, "enrichment", 7, "test-csrf", null);
});

it("refreshes a stale retry rejection without moving the card optimistically", async () => {
  const user = userEvent.setup();
  const ready = {
    ...card,
    stage: "ready" as const,
    state: "ready",
    retry_targets: ["images" as const],
  };
  vi.mocked(getArticlePipeline)
    .mockResolvedValueOnce(board(ready))
    .mockResolvedValue(board({ ...ready, editorial_revision: 8 }));
  vi.mocked(retryArticlePipeline).mockRejectedValueOnce(
    new ApiError(409, "The job changed; refresh and try again."),
  );
  renderAdmin(<ArticlePipeline query="view=pipeline&stage=ready" />);
  await user.click(await screen.findByRole("button", { name: `Move back ${card.title}` }));
  await user.click(screen.getByRole("menuitem", { name: "Retry images" }));
  await waitFor(() => expect(getArticlePipeline).toHaveBeenCalledTimes(2));
  expect(
    screen
      .getByRole("link", { name: card.title })
      .closest("article")
      ?.getAttribute("data-pipeline-stage"),
  ).toBe("ready");
  expect(screen.queryByText("Loading…")).toBeNull();
});

it("notifies a failed retry and keeps the existing card available for another attempt", async () => {
  const user = userEvent.setup();
  vi.mocked(getArticlePipeline).mockResolvedValue(
    board({ ...card, stage: "ready", state: "ready", retry_targets: ["images"] }),
  );
  vi.mocked(retryArticlePipeline).mockRejectedValueOnce(new ApiError(503));
  renderAdmin(<ArticlePipeline query="view=pipeline&stage=ready" />);
  await user.click(await screen.findByRole("button", { name: `Move back ${card.title}` }));
  await user.click(screen.getByRole("menuitem", { name: "Retry images" }));
  await waitFor(() => expect(toast.error).toHaveBeenCalled());
  expect(
    screen
      .getByRole("link", { name: card.title })
      .closest("article")
      ?.getAttribute("data-pipeline-stage"),
  ).toBe("ready");
  expect(
    (screen.getByRole("button", { name: `Move back ${card.title}` }) as HTMLButtonElement).disabled,
  ).toBe(false);
});

it("makes the unpublish effect explicit for Published retry actions", async () => {
  const user = userEvent.setup();
  vi.mocked(getArticlePipeline).mockResolvedValue(
    board({
      ...card,
      stage: "published",
      state: "complete",
      retry_targets: ["enrichment", "analysis", "images"],
    }),
  );
  renderAdmin(<ArticlePipeline query="view=pipeline&stage=published" />);
  await user.click(await screen.findByRole("button", { name: `Move back ${card.title}` }));
  for (const label of ["enrichment", "AI analysis", "images"])
    expect(screen.getByRole("menuitem", { name: `Unpublish and retry ${label}` })).toBeDefined();
  expect(screen.queryByRole("menuitem", { name: /^Retry/ })).toBeNull();
  await user.click(screen.getByRole("menuitem", { name: "Unpublish and retry AI analysis" }));
  expect(retryArticlePipeline).toHaveBeenCalledWith(card.id, "analysis", 7, "test-csrf", "ai-job");
});

it("keeps rejected cards without processing retry targets actionless", async () => {
  vi.mocked(getArticlePipeline).mockResolvedValue(
    board({ ...card, stage: "rejected", state: "complete", retry_targets: [] }),
  );
  renderAdmin(<ArticlePipeline query="view=pipeline&stage=rejected" />);
  const item = (await screen.findByRole("link", { name: card.title })).closest("article")!;
  expect(item.getAttribute("draggable")).not.toBe("true");
  expect(screen.queryByRole("button", { name: `Move back ${card.title}` })).toBeNull();
});

it("collapses a column by double-click and expands it through its accessible control", async () => {
  renderAdmin(<ArticlePipeline query="view=pipeline&stage=images" />);
  await screen.findByRole("link", { name: card.title });
  fireEvent.doubleClick(screen.getByRole("heading", { name: "Images", level: 2 }));
  const expand = screen.getByRole("button", { name: "Expand Images column" });
  expect(expand.getAttribute("aria-expanded")).toBe("false");
  fireEvent.click(expand);
  expect(
    screen.getByRole("button", { name: "Collapse Images column" }).getAttribute("aria-expanded"),
  ).toBe("true");
});

it("hides columns and mobile choices while keeping at least one column visible", async () => {
  renderAdmin(<ArticlePipeline query="view=pipeline&stage=images" />);
  await screen.findByRole("link", { name: card.title });
  fireEvent.click(screen.getByRole("button", { name: /^Columns/ }));
  const filter = screen.getByRole("dialog", { name: "Pipeline columns" });
  fireEvent.click(within(filter).getByRole("checkbox", { name: "Images" }));
  expect(screen.queryByRole("heading", { name: "Images", level: 2 })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: /^Columns/ }));
  fireEvent.click(screen.getByRole("combobox", { name: "Pipeline stage" }));
  expect(screen.queryByRole("option", { name: /^Images/ })).toBeNull();
  fireEvent.click(screen.getByRole("option", { name: /^Ingested/ }));
  fireEvent.click(screen.getByRole("button", { name: /^Columns/ }));
  fireEvent.click(screen.getByRole("button", { name: "Show all columns" }));
  for (const stage of pipelineStages.filter((stage) => stage !== "images"))
    fireEvent.click(screen.getByRole("checkbox", { name: pipelineLabels[stage] }));
  const last = screen.getByRole("checkbox", { name: "Images", checked: true });
  expect((last as HTMLInputElement).disabled).toBe(true);
  fireEvent.click(last);
  expect(screen.getByRole("checkbox", { name: "Images", checked: true })).toBeDefined();
  fireEvent.click(screen.getByRole("button", { name: "Show all columns" }));
  expect(screen.getAllByRole("checkbox", { checked: true })).toHaveLength(pipelineStages.length);
});

it("retains hidden and collapsed columns through polling, query changes, and remounting", async () => {
  vi.useFakeTimers();
  const content = (query: string) => (
    <RefreshSettings>
      <ArticlePipeline query={query} />
    </RefreshSettings>
  );
  let view!: ReturnType<typeof renderAdmin>;
  await act(async () => {
    view = renderAdmin(content("view=pipeline&stage=images"));
  });
  await saveRefresh(0);
  fireEvent.doubleClick(screen.getByRole("heading", { name: "Images", level: 2 }));
  fireEvent.click(screen.getByRole("button", { name: /^Columns/ }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Rejected" }));
  fireEvent.click(screen.getByRole("button", { name: /^Columns/ }));
  await act(() => vi.advanceTimersByTimeAsync(5000));
  expect(getArticlePipeline).toHaveBeenCalledTimes(2);
  expect(screen.getByRole("button", { name: "Expand Images column" })).toBeDefined();
  expect(screen.queryByRole("heading", { name: "Rejected", level: 2 })).toBeNull();
  await act(async () => {
    view.rerender(content("view=pipeline&stage=images&q=python"));
  });
  expect(screen.getByRole("button", { name: "Expand Images column" })).toBeDefined();
  expect(screen.queryByRole("heading", { name: "Rejected", level: 2 })).toBeNull();
  view.unmount();
  await act(async () => {
    renderAdmin(content("view=pipeline&stage=images&q=python"));
  });
  expect(screen.getByRole("button", { name: "Expand Images column" })).toBeDefined();
  expect(screen.queryByRole("heading", { name: "Rejected", level: 2 })).toBeNull();
});

it("uses Pipeline/List views without loading the ordinary article table for the board", async () => {
  navigation.query = "view=pipeline&group_by=source&stage=images&needs_attention=true";
  vi.mocked(getArticlePipeline).mockResolvedValue(groupedBoard());
  renderAdmin(<ResourceList resource="articles" />);
  await screen.findByRole("button", { name: "Expand Engineering Journal articles" });
  expect(listRecords).not.toHaveBeenCalled();
  const views = screen.getByRole("navigation", { name: "Article views" });
  expect(within(views).getByRole("link", { name: "Pipeline" }).getAttribute("aria-current")).toBe(
    "page",
  );
  expect(within(views).getByRole("link", { name: "List" }).getAttribute("href")).toContain(
    "view=list",
  );
  expect(within(views).getByRole("link", { name: "List" }).getAttribute("href")).not.toContain(
    "group_by",
  );
});

it("keeps cards to a linked title and source with an accessible status icon", async () => {
  renderAdmin(<ArticlePipeline query="view=pipeline&stage=images" />);
  const title = await screen.findByRole("link", { name: card.title });
  const item = title.closest("article")!;
  expect(title.getAttribute("href")).toBe(`/content/articles/${card.id}`);
  expect(within(item).getByText(card.source_name!)).toBeDefined();
  expect(within(item).getByRole("img", { name: /^Running/ })).toBeDefined();
  expect(within(item).queryByText("Running")).toBeNull();
  expect(within(item).getAllByRole("link")).toHaveLength(1);
  expect(item.querySelectorAll("p")).toHaveLength(1);
  expect(item.querySelector("time")).toBeNull();
  expect(within(item).queryByLabelText("Processing stages")).toBeNull();
  expect(within(item).queryByText("Images processing")).toBeNull();
  expect(
    screen.queryByText(/Published and rejected columns show articles since|Updated/),
  ).toBeNull();
  expect(screen.queryByRole("button", { name: /^Publish(?: article)?$/i })).toBeNull();
});

it.each([
  ["ingested", "waiting", "amber", "Waiting"],
  ["enrichment", "running", "blue", "Running"],
  ["analysis", "retry_wait", "violet", "Waiting to retry"],
  ["review", "blocked", "rose", "Blocked"],
  ["ready", "ready", "emerald", "Ready"],
  ["published", "complete", "green", "Published"],
  ["rejected", "complete", "rose", "Rejected"],
] as const)(
  "colors %s cards without adding visible status text",
  async (stage, state, hue, label) => {
    vi.mocked(getArticlePipeline).mockResolvedValue(board({ ...card, stage, state, blockers: [] }));
    renderAdmin(<ArticlePipeline query={`view=pipeline&stage=${stage}`} />);
    const item = (await screen.findByRole("link", { name: card.title })).closest("article")!;
    expect(item.getAttribute("data-pipeline-state")).toBe(state);
    expect(item.className).toContain(`${hue}-`);
    expect(within(item).getByRole("img", { name: new RegExp(`^${label}`) })).toBeDefined();
    expect(item.textContent).toBe(`${card.title}${card.source_name}`);
  },
);

it("leaves retry status text and run timing in article readiness", async () => {
  vi.mocked(getArticlePipeline).mockResolvedValue(
    board({
      ...card,
      state: "retry_wait",
      next_check_at: "2026-10-19T12:00:00Z",
      jobs: {
        ...card.jobs,
        enrichment: { ...card.jobs.enrichment!, status: "failed", retryable: true },
        images: {
          ...card.jobs.images!,
          status: "queued",
          available_at: "2026-10-19T09:00:00Z",
        },
      },
    }),
  );
  renderAdmin(<ArticlePipeline query="view=pipeline&stage=images" />);
  await screen.findByRole("img", { name: /^Waiting to retry/ });
  expect(screen.queryByText("Waiting to retry")).toBeNull();
  expect(screen.queryByText(/Retry available/)).toBeNull();
  expect(screen.queryByText(/Next check/)).toBeNull();
  expect(screen.queryByRole("link", { name: "View current run" })).toBeNull();
});

it("keeps blocker details off cards while exposing the blocked status accessibly", async () => {
  vi.mocked(getArticlePipeline).mockResolvedValue(
    board({
      ...card,
      state: "blocked",
      blockers: ["images_failed", "analysis_missing", "review_required"],
      jobs: {
        ...card.jobs,
        enrichment: { ...card.jobs.enrichment!, status: "queued" },
        images: { ...card.jobs.images!, status: "failed", retryable: true },
      },
    }),
  );
  renderAdmin(<ArticlePipeline query="view=pipeline&stage=images" />);
  await screen.findByRole("img", { name: /^Blocked/ });
  expect(screen.queryByText(/Images failed|Analysis missing|Review required|\+2/)).toBeNull();
  expect(screen.queryByRole("link", { name: "Open run to retry" })).toBeNull();
});

it("keeps specialist diagnostics within the existing AI stage detail", async () => {
  vi.mocked(getArticlePipeline).mockResolvedValue(
    board({ ...card, stage: "analysis", current_analysis_step: "overview" }),
  );
  renderAdmin(<ArticlePipeline query="view=pipeline&stage=analysis" />);
  await screen.findByRole("img", { name: /^Running/ });
  expect(screen.queryByText(/Article overview/)).toBeNull();
  expect(
    screen
      .getByRole("link", { name: card.title })
      .closest("section")
      ?.getAttribute("aria-labelledby"),
  ).toBe("pipeline-analysis");
  expect(screen.getAllByRole("heading", { name: "AI analysis", level: 2 })).toHaveLength(1);
});

it("moves an article automatically after five seconds even when general refresh is off", async () => {
  vi.useFakeTimers();
  vi.mocked(getArticlePipeline)
    .mockResolvedValueOnce(board())
    .mockResolvedValue(board({ ...card, stage: "ready", state: "ready", blockers: [] }));
  await act(async () => {
    renderAdmin(
      <RefreshSettings>
        <ArticlePipeline query="view=pipeline" />
      </RefreshSettings>,
    );
  });
  await saveRefresh(0);
  expect(screen.queryByRole("button", { name: /Refresh pipeline/i })).toBeNull();
  await act(() => vi.advanceTimersByTimeAsync(4999));
  expect(getArticlePipeline).toHaveBeenCalledTimes(1);
  await act(() => vi.advanceTimersByTimeAsync(1));
  expect(screen.getAllByRole("link", { name: card.title })).toHaveLength(1);
  expect(
    screen
      .getByRole("link", { name: card.title })
      .closest("section")
      ?.getAttribute("aria-labelledby"),
  ).toBe("pipeline-ready");
});

it("withdraws the previous board after preparation starts and recovers automatically", async () => {
  vi.useFakeTimers();
  vi.mocked(getArticlePipeline)
    .mockResolvedValueOnce(board())
    .mockRejectedValueOnce(
      new ApiError(503, "Analytics are being prepared.", {}, "analytics_preparing"),
    )
    .mockResolvedValue(board({ ...card, stage: "ready", state: "ready", blockers: [] }));
  await act(async () => {
    renderAdmin(
      <RefreshSettings>
        <ArticlePipeline query="view=pipeline" />
      </RefreshSettings>,
    );
  });
  await saveRefresh(0);
  expect(screen.getByRole("link", { name: card.title })).toBeTruthy();
  await act(() => vi.advanceTimersByTimeAsync(5000));
  expect(screen.queryByRole("link", { name: card.title })).toBeNull();
  expect(screen.queryByRole("region", { name: "Article publication pipeline" })).toBeNull();
  expect(screen.queryByRole("button", { name: `Move back ${card.title}` })).toBeNull();
  expect(screen.getByRole("alert").textContent).toContain("Analytics are being prepared.");
  await act(() => vi.advanceTimersByTimeAsync(5000));
  expect(screen.getByRole("link", { name: card.title })).toBeTruthy();
  expect(screen.queryByRole("alert")).toBeNull();
  expect(getArticlePipeline).toHaveBeenCalledTimes(3);
});

it("honors a slower saved interval for automatic pipeline updates", async () => {
  vi.useFakeTimers();
  await act(async () => {
    renderAdmin(
      <RefreshSettings>
        <ArticlePipeline query="view=pipeline" />
      </RefreshSettings>,
    );
  });
  await saveRefresh(60);
  await act(() => vi.advanceTimersByTimeAsync(59999));
  expect(getArticlePipeline).toHaveBeenCalledTimes(1);
  await act(() => vi.advanceTimersByTimeAsync(1));
  expect(getArticlePipeline).toHaveBeenCalledTimes(2);
});

it("loads one bounded lane page and deduplicates repeated moving cards", async () => {
  const initial = board();
  initial.columns.find((column) => column.stage === "images")!.next_cursor = 20;
  vi.mocked(getArticlePipeline).mockResolvedValue(initial);
  vi.mocked(getArticlePipelineColumn).mockResolvedValue({
    stage: "images",
    total: 2,
    items: [card, { ...card, id: "article-two", title: "A second article" }],
    next_cursor: null,
  });
  renderAdmin(<ArticlePipeline query="view=pipeline&needs_attention=true" />);
  fireEvent.click(await screen.findByRole("button", { name: "Load more images articles" }));
  await screen.findByRole("link", { name: "A second article" });
  expect(getArticlePipelineColumn).toHaveBeenCalledWith(
    "images",
    "view=pipeline&needs_attention=true",
    20,
    expect.any(AbortSignal),
  );
  expect(screen.getAllByRole("link", { name: card.title })).toHaveLength(1);
});

it("keeps running cards first when later pages replace a waiting card", async () => {
  const running = {
    ...card,
    id: "running",
    title: "Existing running",
    discovered_at: "2026-10-19T08:00:00Z",
  };
  const waiting = {
    ...card,
    id: "z-waiting",
    title: "Waiting already loaded",
    state: "waiting",
    discovered_at: "2026-10-19T06:00:00Z",
  };
  const moving = {
    ...card,
    id: "moving",
    title: "Newly running",
    state: "waiting",
    discovered_at: "2026-10-19T09:00:00Z",
  };
  const initial = board();
  const lane = initial.columns.find((column) => column.stage === "images")!;
  lane.items = [running, waiting, moving];
  lane.total = 4;
  lane.next_cursor = 20;
  vi.mocked(getArticlePipeline).mockResolvedValue(initial);
  vi.mocked(getArticlePipelineColumn).mockResolvedValue({
    stage: "images",
    total: 4,
    items: [
      { ...moving, state: "running" },
      { ...waiting, id: "a-waiting", title: "Waiting on next page" },
    ],
    next_cursor: null,
  });
  renderAdmin(<ArticlePipeline query="view=pipeline&stage=images" />);
  fireEvent.click(await screen.findByRole("button", { name: "Load more images articles" }));
  await screen.findByRole("link", { name: "Waiting on next page" });
  const section = screen.getByRole("heading", { name: "Images", level: 2 }).closest("section")!;
  expect(
    within(section)
      .getAllByRole("link")
      .map((link) => link.textContent),
  ).toEqual([
    "Existing running",
    "Newly running",
    "Waiting on next page",
    "Waiting already loaded",
  ]);
});

it("preserves discovery order within the same millisecond before comparing IDs", async () => {
  const older = {
    ...card,
    id: "z-older",
    title: "Earlier by a microsecond",
    discovered_at: "2026-10-19T08:00:00.000001Z",
  };
  const newer = {
    ...card,
    id: "a-newer",
    title: "Later by a microsecond",
    discovered_at: "2026-10-19T08:00:00.000002Z",
  };
  const initial = board(older);
  initial.columns.find((column) => column.stage === "images")!.items = [newer, older];
  vi.mocked(getArticlePipeline).mockResolvedValue(initial);
  renderAdmin(<ArticlePipeline query="view=pipeline&stage=images" />);
  await screen.findByRole("link", { name: older.title });
  const lane = screen.getByRole("heading", { name: "Images", level: 2 }).closest("section")!;
  expect(
    within(lane)
      .getAllByRole("link")
      .map((link) => link.textContent),
  ).toEqual([older.title, newer.title]);
});

it("shows publication checks and the delayed image run rather than a false all-clear", async () => {
  renderAdmin(<ArticleReadiness id={card.id} />);
  await screen.findByText("An image lookup is still running.");
  expect(screen.queryByText("All publication requirements are met.")).toBeNull();
  expect(screen.getByRole("list", { name: "Publication readiness checks" })).toBeDefined();
  expect(screen.getAllByRole("link", { name: "View run and logs" })).toHaveLength(3);
  expect(screen.queryByRole("button", { name: /Refresh readiness/i })).toBeNull();
});

it("updates readiness automatically after processing completes", async () => {
  vi.useFakeTimers();
  vi.mocked(getArticlePipelineDetail)
    .mockResolvedValueOnce(detail)
    .mockResolvedValue({
      ...detail,
      stage: "ready",
      state: "ready",
      blockers: [],
      can_publish: true,
    });
  await act(async () => {
    renderAdmin(
      <RefreshSettings>
        <ArticleReadiness id={card.id} />
      </RefreshSettings>,
    );
  });
  await saveRefresh(0);
  expect(screen.queryByText("All publication requirements are met.")).toBeNull();
  await act(() => vi.advanceTimersByTimeAsync(5000));
  expect(screen.getByText("All publication requirements are met.")).toBeDefined();
});

it("nests specialist progress and scheduled retry metadata inside the single AI analysis run", async () => {
  vi.mocked(getArticlePipelineDetail).mockResolvedValue({
    ...detail,
    stage: "analysis",
    state: "retry_wait",
    jobs: { ...detail.jobs, analysis: { ...detail.jobs.analysis!, status: "queued" } },
    analysis_steps: [
      {
        name: "identifying_title",
        label: "Identifying title",
        status: "succeeded",
        prompt_version: "identifying-title-v1",
        attempts: 1,
      },
      {
        name: "overview",
        label: "Article overview",
        status: "failed",
        prompt_version: "overview-v1",
        attempts: 2,
        available_at: "2026-10-19T09:15:00Z",
        error: "provider_rate_limited",
      },
    ],
  });
  renderAdmin(<ArticleReadiness id={card.id} />);
  const steps = await screen.findByRole("list", { name: "AI analysis steps" });
  expect(
    within(steps.closest("li")!).getByText("AI analysis", { selector: "strong" }),
  ).toBeDefined();
  expect(within(steps).getByText("Waiting to retry")).toBeDefined();
  expect(within(steps).getByText("Provider rate limited")).toBeDefined();
  expect(within(steps).getAllByRole("listitem")).toHaveLength(2);
  expect(within(steps).queryByRole("button")).toBeNull();
});

it.each([
  ["published", "This article is published. Unpublish it before changing or reprocessing it."],
  ["rejected", "This article was rejected. The editorial decision is complete."],
] as const)(
  "describes the %s terminal state without claiming more processing is pending",
  async (stage, message) => {
    vi.mocked(getArticlePipelineDetail).mockResolvedValue({
      ...detail,
      stage,
      state: "complete",
      can_publish: false,
      checks: [],
      blockers: [],
      jobs: {},
      allowed_actions: [],
      advance_label: null,
    });
    renderAdmin(<ArticleReadiness id={card.id} />);
    await screen.findByText(message);
    expect(
      screen.queryByText(
        "Publication waits until processing and editorial requirements are complete.",
      ),
    ).toBeNull();
    expect(screen.queryByRole("list", { name: "Publication readiness checks" })).toBeNull();
    expect(screen.queryByRole("list", { name: "Current processing runs" })).toBeNull();
    expect(screen.queryByText("Not started")).toBeNull();
  },
);

it("requests only a server-allowed next step with the current revision and CSRF token", async () => {
  vi.mocked(getArticlePipelineDetail).mockResolvedValue({
    ...detail,
    state: "blocked",
    allowed_actions: ["advance"],
    advance_label: "Prepare article images",
  });
  renderAdmin(<ArticleReadiness id={card.id} />);
  fireEvent.click(await screen.findByRole("button", { name: "Prepare article images" }));
  await waitFor(() => expect(advanceArticlePipeline).toHaveBeenCalledWith(card.id, 7, "test-csrf"));
  await waitFor(() => expect(getArticlePipelineDetail).toHaveBeenCalledTimes(2));
});

it("reloads readiness after a stale recovery rejection without moving the article locally", async () => {
  vi.mocked(getArticlePipelineDetail).mockResolvedValue({
    ...detail,
    state: "blocked",
    allowed_actions: ["advance"],
  });
  vi.mocked(advanceArticlePipeline).mockRejectedValue(
    new ApiError(409, "Article changed; reload before continuing"),
  );
  renderAdmin(<ArticleReadiness id={card.id} />);
  fireEvent.click(await screen.findByRole("button", { name: "Run next processing step" }));
  await screen.findByText("Article changed; reload before continuing");
  await waitFor(() => expect(getArticlePipelineDetail).toHaveBeenCalledTimes(2));
});

it("disables publication while a required processing stage is incomplete", async () => {
  renderAdmin(
    <ResourceWorkflow identity={{ resource: "articles", id: card.id }} action="review" />,
  );
  fireEvent.click(await screen.findByRole("combobox", { name: /Decision/ }));
  fireEvent.click(await screen.findByRole("option", { name: /^Publish$/ }));
  await screen.findByRole("link", { name: "Open publication readiness" });
  const submit = screen.getByRole("button", { name: "Apply decision" });
  expect((submit as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(submit);
  expect(adminArticleReview).not.toHaveBeenCalled();
  expect(screen.queryByRole("button", { name: /Refresh publication readiness/i })).toBeNull();
});

it("refreshes the article revision with readiness so publication recovers without reloading", async () => {
  vi.useFakeTimers();
  const original = {
    id: card.id,
    title: card.title,
    editorial_revision: 7,
    publication_status: "unpublished",
    review_status: "approved",
    publication_blockers: [],
  };
  vi.mocked(getRecord)
    .mockResolvedValueOnce(original)
    .mockResolvedValue({ ...original, editorial_revision: 8 });
  vi.mocked(getArticlePipelineDetail)
    .mockResolvedValueOnce(detail)
    .mockResolvedValue({
      ...detail,
      stage: "ready",
      state: "ready",
      can_publish: true,
      blockers: [],
      editorial_revision: 8,
    });
  await act(async () => {
    renderAdmin(
      <RefreshSettings>
        <ResourceWorkflow identity={{ resource: "articles", id: card.id }} action="review" />
      </RefreshSettings>,
    );
  });
  await saveRefresh(0);
  fireEvent.click(screen.getByRole("combobox", { name: /Decision/ }));
  fireEvent.click(screen.getByRole("option", { name: /^Publish$/ }));
  expect(
    (screen.getByRole("button", { name: "Apply decision" }) as HTMLButtonElement).disabled,
  ).toBe(true);
  await act(() => vi.advanceTimersByTimeAsync(5000));
  expect(
    (screen.getByRole("button", { name: "Apply decision" }) as HTMLButtonElement).disabled,
  ).toBe(false);
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Apply decision" })));
  expect(adminArticleReview).toHaveBeenCalledWith(
    card.id,
    { action: "publish", expected_revision: 8, note: null },
    { headers: { "X-CSRF-Token": "fixture" } },
  );
});

it("shows the generated title beside AI metadata while preserving the original release title", async () => {
  vi.mocked(getRecord).mockResolvedValue({
    id: card.id,
    title: "0.114.0",
    ai_title: "FastAPI releases version 0.114.0",
    ai_summary: "A source-grounded release overview.",
    publication_blockers: [],
    sources: [],
    topics: [],
    tags: [],
  });
  renderAdmin(<ResourceDetail identity={{ resource: "articles", id: card.id }} />);
  const panel = (await screen.findByRole("heading", { name: "AI-generated metadata" })).closest(
    "section",
  )!;
  expect(within(panel).getByText("FastAPI releases version 0.114.0")).toBeDefined();
  expect(screen.getByRole("heading", { level: 1, name: "0.114.0" })).toBeDefined();
  expect(screen.getByText("Title", { selector: "dt" }).nextElementSibling?.textContent).toBe(
    "0.114.0",
  );
});

it.each([null, "", "  "])(
  "omits a missing generated title from AI metadata (%j)",
  async (ai_title) => {
    vi.mocked(getRecord).mockResolvedValue({
      id: card.id,
      title: "Original source title",
      ai_title,
      publication_blockers: [],
      sources: [],
      topics: [],
      tags: [],
    });
    renderAdmin(<ResourceDetail identity={{ resource: "articles", id: card.id }} />);
    const panel = (await screen.findByRole("heading", { name: "AI-generated metadata" })).closest(
      "section",
    )!;
    expect(within(panel).queryByText("Ai title")).toBeNull();
  },
);
