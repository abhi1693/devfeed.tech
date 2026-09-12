// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { renderAdmin } from "./render-admin";
import { AutomationOverview } from "@/components/organisms/automation-overview";
import { SourcePublicationPolicy } from "@/components/organisms/source-publication-policy";
import { AutomationHistory } from "@/components/organisms/automation-history";
import * as api from "@/lib/api/generated/admin";
import type { AutomationOverview as AutomationData, SourceOut } from "@/lib/api/generated/models";
import { notify, notifyFailure } from "@/lib/notifications";

vi.mock("@/lib/api/generated/admin", () => ({
  adminAutomationRecover: vi.fn(),
  adminSourcePublicationPolicy: vi.fn(),
  adminPublicationDecisions: vi.fn(),
  adminPublicationPolicyHistory: vi.fn(),
}));
vi.mock("@/lib/notifications", () => ({
  notify: { success: vi.fn(), warning: vi.fn() },
  notifyFailure: vi.fn(),
}));
beforeEach(() => vi.clearAllMocks());
afterEach(cleanup);

const data: AutomationData = {
  analysis_duration_ms: 1200,
  analysis_tokens: 850,
  automatic_publication_percent: 50,
  median_ingestion_to_publication_seconds: 120,
  published_in_window: 4,
  published_without_intervention: 2,
  usage_reported_runs: 3,
  blockers: [
    {
      code: "missing_primary_topic",
      label: "Missing primary topic",
      count: 1,
      action: "analyze",
      targets: [{ id: "article-1", title: "Routing guide", kind: "article", revision: 7 }],
    },
  ],
};

it("recovers the selected article with its displayed revision and session token", async () => {
  vi.mocked(api.adminAutomationRecover).mockResolvedValue({ status: "queued", job_id: "job-1" });
  const refresh = vi.fn();
  renderAdmin(<AutomationOverview data={data} onChange={refresh} />);
  expect(screen.getByText("50%")).toBeTruthy();
  expect(screen.getByText("2 min")).toBeTruthy();
  fireEvent.click(screen.getByText("Missing primary topic"));
  expect(screen.getByRole("link", { name: "Routing guide" }).getAttribute("href")).toBe(
    "/content/articles/article-1",
  );
  fireEvent.click(screen.getByRole("button", { name: "Analyze again" }));
  await waitFor(() => expect(refresh).toHaveBeenCalledOnce());
  expect(api.adminAutomationRecover).toHaveBeenCalledWith(
    "article-1",
    "analyze",
    { expected_revision: 7 },
    { headers: { "X-CSRF-Token": "test-csrf" } },
  );
  expect(notify.success).toHaveBeenCalledWith("Recovery job queued");
});

it("retains blockers and reports a failed recovery without claiming success", async () => {
  vi.mocked(api.adminAutomationRecover).mockRejectedValue(new Error("Conflict"));
  const refresh = vi.fn();
  renderAdmin(<AutomationOverview data={data} onChange={refresh} />);
  fireEvent.click(screen.getByText("Missing primary topic"));
  fireEvent.click(screen.getByRole("button", { name: "Analyze again" }));
  await waitFor(() => expect(notifyFailure).toHaveBeenCalledOnce());
  expect(refresh).not.toHaveBeenCalled();
  expect(screen.getByRole("link", { name: "Routing guide" })).toBeTruthy();
});

it("keeps empty publication metrics unavailable and links relationship blockers correctly", () => {
  renderAdmin(
    <AutomationOverview
      data={{
        ...data,
        automatic_publication_percent: null,
        median_ingestion_to_publication_seconds: null,
        blockers: [
          {
            code: "relationship",
            label: "Relationship evidence",
            count: 1,
            targets: [{ id: "relation-1", title: "Uses Angular", kind: "relationship-proposal" }],
          },
        ],
      }}
      onChange={vi.fn()}
    />,
  );
  expect(screen.getAllByText("—")).toHaveLength(2);
  fireEvent.click(screen.getByText("Relationship evidence"));
  expect(screen.getByRole("link", { name: "Uses Angular" }).getAttribute("href")).toBe(
    "/taxonomy/relationships/proposals/relation-1",
  );
});

it("requires a saved preview before auto mode and uses the returned revision", async () => {
  vi.mocked(api.adminSourcePublicationPolicy).mockResolvedValue({
    publication_policy: "preview",
    publication_policy_revision: 4,
  } as SourceOut);
  renderAdmin(<SourcePublicationPolicy id="source-1" mode="manual" revision={3} approved />);
  const select = screen.getByRole("combobox", { name: "Publication mode" });
  const automatic = screen.getByRole("option", {
    name: "Publish eligible articles automatically",
  }) as HTMLOptionElement;
  expect(automatic.disabled).toBe(true);
  fireEvent.change(select, { target: { value: "preview" } });
  fireEvent.click(screen.getByRole("button", { name: "Save publication mode" }));
  await waitFor(() => expect(automatic.disabled).toBe(false));
  expect(api.adminSourcePublicationPolicy).toHaveBeenLastCalledWith(
    "source-1",
    { mode: "preview", expected_revision: 3 },
    { headers: { "X-CSRF-Token": "test-csrf" } },
  );
  vi.mocked(api.adminSourcePublicationPolicy).mockResolvedValue({
    publication_policy: "auto",
    publication_policy_revision: 5,
  } as SourceOut);
  fireEvent.change(select, { target: { value: "auto" } });
  fireEvent.click(screen.getByRole("button", { name: "Save publication mode" }));
  await waitFor(() =>
    expect(api.adminSourcePublicationPolicy).toHaveBeenLastCalledWith(
      "source-1",
      { mode: "auto", expected_revision: 4 },
      { headers: { "X-CSRF-Token": "test-csrf" } },
    ),
  );
});

it("does not unlock automatic publication when saving preview fails", async () => {
  vi.mocked(api.adminSourcePublicationPolicy).mockRejectedValue(new Error("Stale revision"));
  renderAdmin(<SourcePublicationPolicy id="source-1" mode="manual" revision={3} approved />);
  fireEvent.change(screen.getByRole("combobox"), { target: { value: "preview" } });
  fireEvent.click(screen.getByRole("button", { name: "Save publication mode" }));
  await waitFor(() => expect(notifyFailure).toHaveBeenCalledOnce());
  expect(
    (
      screen.getByRole("option", {
        name: "Publish eligible articles automatically",
      }) as HTMLOptionElement
    ).disabled,
  ).toBe(true);
});

it("disables publication policy changes for unapproved sources", () => {
  renderAdmin(
    <SourcePublicationPolicy id="source-1" mode="manual" revision={0} approved={false} />,
  );
  expect((screen.getByRole("combobox") as HTMLSelectElement).disabled).toBe(true);
  expect(
    (screen.getByRole("button", { name: "Save publication mode" }) as HTMLButtonElement).disabled,
  ).toBe(true);
});

it("paginates retained publication decisions", async () => {
  vi.mocked(api.adminPublicationDecisions).mockResolvedValue({
    items: [
      {
        id: "decision-1",
        created_at: "2026-09-10T00:00:00Z",
        decision: { status: "would_publish", policy_version: "trusted-source-v1" },
      },
    ],
    total: 21,
    limit: 20,
    offset: 0,
  });
  renderAdmin(<AutomationHistory resource="articles" id="article-1" />);
  await screen.findByText(/21 records/);
  fireEvent.click(screen.getByRole("button", { name: "Next decisions" }));
  await waitFor(() =>
    expect(api.adminPublicationDecisions).toHaveBeenLastCalledWith(
      "article-1",
      { offset: 20, limit: 20 },
      { signal: expect.any(AbortSignal) },
    ),
  );
});

it("shows full mode authority without the manual publication controls", () => {
  renderAdmin(
    <SourcePublicationPolicy id="source-1" mode="manual" revision={0} approved fullAutomation />,
  );
  expect(screen.getByText(/Full automation is enabled/)).toBeTruthy();
  expect(screen.queryByRole("combobox")).toBeNull();
});

it("shows automatic progress without requiring recovery clicks in full mode", () => {
  renderAdmin(<AutomationOverview data={{ ...data, full_automation: true }} onChange={vi.fn()} />);
  expect(screen.getByText("Automation progress")).toBeTruthy();
  expect(screen.getByText(/No manual review is required/)).toBeTruthy();
  fireEvent.click(screen.getByText("Missing primary topic"));
  expect(screen.queryByRole("button", { name: "Analyze again" })).toBeNull();
});
