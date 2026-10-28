// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { renderAdmin } from "./render-admin";
import { SourcePublicationPolicy } from "@/components/organisms/source-publication-policy";
import { AutomationHistory } from "@/components/organisms/automation-history";
import * as api from "@/lib/api/generated/admin";
import type { SourceOut } from "@/lib/api/generated/models";
import { notifyFailure } from "@/lib/notifications";

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
  expect(screen.getByText(/Automatic publication is enabled/)).toBeTruthy();
  expect(screen.queryByRole("combobox")).toBeNull();
});
