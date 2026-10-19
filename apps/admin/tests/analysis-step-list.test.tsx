// @vitest-environment jsdom
import { afterEach, expect, it } from "vitest";
import { cleanup, screen, within } from "@testing-library/react";
import { renderAdmin } from "./render-admin";
import { AnalysisStepList } from "@/components/molecules/analysis-step-list";
import type { AnalysisStepOut } from "@/lib/api/generated/models";
import { analysisStepLabel } from "@/lib/article-pipeline";

afterEach(cleanup);

const steps: AnalysisStepOut[] = [
  "developer_relevance",
  "page_kind",
  "language",
  "content_type",
  "content_format",
  "topics",
  "tags",
  "identifying_title",
  "overview",
  "description",
].map((name, index) => ({
  name,
  label: analysisStepLabel(name),
  status: index < 8 ? "succeeded" : index === 8 ? "running" : "pending",
  attempts: index < 9 ? 1 : 0,
  prompt_version: `${name}-v1`,
}));

it("keeps server step order and identifies the running step within one analysis list", () => {
  renderAdmin(<AnalysisStepList steps={steps} jobStatus="running" />);
  const list = screen.getByRole("list", { name: "AI analysis steps" });
  const rows = within(list).getAllByRole("listitem");
  expect(rows).toHaveLength(10);
  steps.forEach((step, index) => expect(within(rows[index]).getByText(step.label)).toBeDefined());
  expect(rows[8].getAttribute("aria-current")).toBe("step");
  expect(screen.getByText("Current step: Article overview")).toBeDefined();
  expect(within(rows[8]).getByText("Running")).toBeDefined();
  expect(within(rows[9]).getByText("Waiting")).toBeDefined();
});

it("shows a queued step retry timestamp and treats its error as plain text", () => {
  const message = '<img src="invalid" onerror="alert(1)"> Provider rate limit';
  renderAdmin(
    <AnalysisStepList
      steps={[
        {
          ...steps[8],
          status: "failed",
          attempts: 2,
          available_at: "2026-10-19T09:15:00Z",
          error: message,
        },
      ]}
      jobStatus="queued"
    />,
  );
  expect(screen.getByText("Waiting to retry")).toBeDefined();
  expect(screen.getByText("Prompt overview-v1 · 2 attempts")).toBeDefined();
  expect(screen.getByText(message)).toBeDefined();
  expect(screen.queryByRole("img")).toBeNull();
  expect(
    screen
      .getByText(/Retry available/)
      .querySelector("time")
      ?.getAttribute("datetime"),
  ).toBe("2026-10-19T09:15:00.000Z");
});

it("shows final failure and skipped steps without promising a scheduled retry", () => {
  renderAdmin(
    <AnalysisStepList
      steps={[
        { ...steps[8], status: "failed", error: "The overview could not be validated." },
        { ...steps[9], status: "skipped" },
      ]}
      jobStatus="failed"
    />,
  );
  expect(screen.getByText("Failed")).toBeDefined();
  expect(screen.getByText("Skipped")).toBeDefined();
  expect(screen.queryByText(/Retry available|Waiting to retry/)).toBeNull();
});
