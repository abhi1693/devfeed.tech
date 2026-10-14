// @vitest-environment jsdom
import { cleanup, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { renderAdmin } from "./render-admin";
import { ResourceList } from "@/components/organisms/resource-list";
import { ResourceDetail } from "@/components/organisms/resource-detail";
import { retryRecordJob } from "@/lib/resource-api";
import { adminJobGet, adminJobLogs, adminJobRetry, adminJobsList } from "@/lib/api/generated/admin";
import type { AdminJobOut } from "@/lib/api/generated/models";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/lib/use-refresh-interval", () => ({ useRefreshInterval: () => 0 }));
vi.mock("@/lib/api/generated/admin", () => ({
  adminJobsList: vi.fn(),
  adminJobGet: vi.fn(),
  adminJobLogs: vi.fn(),
  adminJobRetry: vi.fn(),
}));
const productId = "11111111-1111-4111-8111-111111111111";
const image: AdminJobOut & { product_id: string } = {
  id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  kind: "images",
  target_name: "API Checker",
  product_id: productId,
  status: "failed",
  retryable: true,
  attempts: 1,
  created_at: "2026-10-11T10:00:00Z",
  available_at: "2026-10-11T10:00:00Z",
  finished_at: "2026-10-11T10:01:00Z",
  error: "Product page unavailable",
  details: { operation: "product-image" },
};
const logo = {
  ...image,
  id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
  details: { operation: "product-logo" },
};
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(adminJobsList).mockResolvedValue({
    items: [image, logo],
    total: 2,
    offset: 0,
    limit: 25,
  });
  vi.mocked(adminJobGet).mockResolvedValue(image);
  vi.mocked(adminJobLogs).mockResolvedValue({
    items: [],
    next_cursor: null,
    has_more: false,
    truncated: false,
    unreadable_entries: 0,
    retention_seconds: 604800,
    max_entries: 1000,
    job_status: "failed",
    attempts: 1,
  });
});
afterEach(cleanup);

it("identifies product image and logo jobs by operation and product subject", async () => {
  renderAdmin(<ResourceList resource="image-jobs" />);
  const table = await screen.findByRole("table", { name: "Images" });
  expect(within(table).getByRole("columnheader", { name: "Operation" })).toBeTruthy();
  expect(within(table).getByRole("columnheader", { name: "Subject" })).toBeTruthy();
  expect(within(table).getByText("Product featured image")).toBeTruthy();
  expect(within(table).getByText("Product logo")).toBeTruthy();
  expect(
    within(table)
      .getAllByRole("link", { name: "API Checker" })
      .map((link) => link.getAttribute("href")),
  ).toEqual([`/partnerships/products/${productId}`, `/partnerships/products/${productId}`]);
  expect(table.querySelector('a[href^="/content/articles/"]')).toBeNull();
  expect(adminJobsList).toHaveBeenCalledWith("images", expect.anything(), expect.anything());
});

it("links a product image run to the product rather than an absent article", async () => {
  renderAdmin(<ResourceDetail identity={{ resource: "image-jobs", id: image.id }} />);
  await screen.findByRole("heading", { name: "Run aaaaaaaa" });
  expect(screen.getByRole("link", { name: "API Checker" }).getAttribute("href")).toBe(
    `/partnerships/products/${productId}`,
  );
  expect(adminJobGet).toHaveBeenCalledWith("images", image.id, { signal: expect.any(AbortSignal) });
  expect(screen.queryByText(/^Article:/)).toBeNull();
});

it("retains article, topic, and source subject links alongside product media jobs", async () => {
  const jobs: AdminJobOut[] = [
    {
      ...image,
      id: "article-image",
      product_id: null,
      article_id: "article-1",
      target_name: "API tutorial",
      details: { operation: "store" },
    },
    {
      ...image,
      id: "topic-logo",
      product_id: null,
      topic_id: "topic-1",
      target_name: "OpenAPI",
      details: { operation: "topic-logo" },
    },
    {
      ...image,
      id: "source-logo",
      product_id: null,
      source_id: "source-1",
      target_name: "Engineering Journal",
      details: { operation: "source-logo" },
    },
  ];
  vi.mocked(adminJobsList).mockResolvedValue({ items: jobs, total: 3, offset: 0, limit: 25 });
  renderAdmin(<ResourceList resource="image-jobs" />);
  const table = await screen.findByRole("table", { name: "Images" });
  for (const [name, href] of [
    ["API tutorial", "/content/articles/article-1"],
    ["OpenAPI", "/taxonomy/topics/topic-1"],
    ["Engineering Journal", "/content/sources/source-1"],
  ]) {
    expect(within(table).getByRole("link", { name }).getAttribute("href")).toBe(href);
  }
});

it.each([image, logo])(
  "retries the original $details.operation job through Images with CSRF",
  async (job) => {
    await retryRecordJob("image-jobs", { ...job }, "images-csrf");
    expect(adminJobRetry).toHaveBeenCalledWith("images", job.id, {
      headers: { "X-CSRF-Token": "images-csrf" },
    });
  },
);
