// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ArticleMaintenance } from "@/components/organisms/article-maintenance";
import { reviewArticleMaintenance } from "@/lib/article-pipeline";
import type { MaintenanceDetail } from "@/lib/api/generated/models";
import { ApiError } from "@/lib/api/client";
import { renderAdmin } from "./render-admin";

vi.mock("@/lib/article-pipeline", async (original) => ({
  ...(await original<typeof import("@/lib/article-pipeline")>()),
  reviewArticleMaintenance: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() } }));

const replacement: MaintenanceDetail["replacement"] = {
  title: "A reviewed replacement",
  summary: "The new overview remains private until approved.",
  description: "A corrected source summary.",
  image_url: "https://images.example.test/replacement.webp",
  language: "en",
  content_type: "tutorial",
  content_format: "article",
  topics: [{ id: "topic-one", name: "Python", role: "primary" }],
  tags: [{ id: "tag-one", name: "Async I/O" }],
};
const value: MaintenanceDetail = {
  id: "refresh-one",
  article_id: "article-one",
  target: "analysis",
  status: "review",
  version: 4,
  created_at: "2026-10-30T08:00:00Z",
  completed_at: null,
  ready: true,
  reasons: [],
  allowed_actions: ["approve", "reject"],
  jobs: {
    analysis: {
      id: "private-analysis",
      status: "succeeded",
      outcome: "applied",
      available_at: "2026-10-30T08:00:00Z",
      finished_at: "2026-10-30T08:01:00Z",
      retryable: false,
    },
  },
  retry_targets: ["analysis"],
  replacement,
};
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(reviewArticleMaintenance).mockResolvedValue({
    ...value,
    status: "promoted",
    allowed_actions: [],
  });
});
afterEach(cleanup);

it("shows named replacement classifications and private jobs without replacing the public article", () => {
  renderAdmin(<ArticleMaintenance value={value} onUpdated={vi.fn()} />);
  const preview = screen.getByRole("region", { name: "Replacement preview" });
  expect(within(preview).getByText(replacement.title)).toBeDefined();
  expect(within(preview).getByText("Python (primary)")).toBeDefined();
  expect(within(preview).getByText("Async I/O")).toBeDefined();
  expect(screen.getByText(/Readers keep seeing the approved article/)).toBeDefined();
  expect(screen.getByRole("link", { name: "AI analysis run" }).getAttribute("href")).toContain(
    "private-analysis",
  );
  expect(screen.queryByText("topic-one")).toBeNull();
});

it("submits explicit approval with the displayed version and the authenticated CSRF token", async () => {
  const user = userEvent.setup();
  const onUpdated = vi.fn();
  renderAdmin(<ArticleMaintenance value={value} onUpdated={onUpdated} />);
  await user.type(screen.getByRole("textbox", { name: /Review note/ }), "  Checked source  ");
  await user.click(screen.getByRole("button", { name: "Approve replacement" }));
  expect(reviewArticleMaintenance).toHaveBeenCalledWith(
    "article-one",
    "refresh-one",
    { action: "approve", expected_version: 4, note: "Checked source" },
    "test-csrf",
  );
  expect(onUpdated).toHaveBeenCalledOnce();
});

it("requires a meaningful discard note and leaves approval unavailable while processing", async () => {
  const user = userEvent.setup();
  renderAdmin(
    <ArticleMaintenance
      value={{
        ...value,
        status: "active",
        ready: false,
        allowed_actions: ["reject"],
        reasons: ["Analysis is still running."],
      }}
      onUpdated={vi.fn()}
    />,
  );
  expect(screen.queryByRole("button", { name: "Approve replacement" })).toBeNull();
  const discard = screen.getByRole("button", { name: "Discard refresh" }) as HTMLButtonElement;
  expect(discard.disabled).toBe(true);
  await user.type(screen.getByRole("textbox", { name: /Review note/ }), "   ");
  expect(discard.disabled).toBe(true);
  await user.type(screen.getByRole("textbox", { name: /Review note/ }), "Source correction needed");
  await user.click(discard);
  expect(reviewArticleMaintenance).toHaveBeenCalledWith(
    "article-one",
    "refresh-one",
    { action: "reject", expected_version: 4, note: "Source correction needed" },
    "test-csrf",
  );
});

it("reloads a stale version after a conflict without claiming approval succeeded", async () => {
  const user = userEvent.setup();
  const onUpdated = vi.fn();
  vi.mocked(reviewArticleMaintenance).mockRejectedValue(new ApiError(409, "Version changed"));
  renderAdmin(<ArticleMaintenance value={value} onUpdated={onUpdated} />);
  await user.click(screen.getByRole("button", { name: "Approve replacement" }));
  await screen.findByText("This refresh changed. Reloading its current version before review.");
  expect(onUpdated).toHaveBeenCalledOnce();
});

it("prevents competing actions until the review request finishes", async () => {
  const user = userEvent.setup();
  let finish!: (value: MaintenanceDetail) => void;
  vi.mocked(reviewArticleMaintenance).mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  renderAdmin(<ArticleMaintenance value={value} onUpdated={vi.fn()} />);
  await user.type(screen.getByRole("textbox", { name: /Review note/ }), "Reviewed");
  await user.click(screen.getByRole("button", { name: "Approve replacement" }));
  expect(
    (screen.getByRole("button", { name: "Discard refresh" }) as HTMLButtonElement).disabled,
  ).toBe(true);
  await user.click(screen.getByRole("button", { name: "Discard refresh" }));
  expect(reviewArticleMaintenance).toHaveBeenCalledOnce();
  finish({ ...value, status: "promoted" });
  await waitFor(() =>
    expect(
      (screen.getByRole("button", { name: "Approve replacement" }) as HTMLButtonElement).disabled,
    ).toBe(false),
  );
});
