// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AiConnection } from "@/components/organisms/ai-connection";
import { adminAiConnection, adminAiLogin, adminAiLoginCancel } from "@/lib/api/generated/admin";
import type { CodexStatus, DeviceLogin } from "@/lib/api/generated/models";
import { ApiError } from "@/lib/api/client";

vi.mock("@/lib/api/generated/admin", () => ({ adminAiConnection: vi.fn(), adminAiLogin: vi.fn(), adminAiLoginCancel: vi.fn() }));
vi.mock("@/lib/notifications", () => ({ notifyFailure: vi.fn() }));

let status: CodexStatus;
const login: DeviceLogin = { login_id: "test-login", status: "pending", user_code: "ABCD-1234", verification_url: "https://auth.openai.com/codex/device", expires_at: "2026-09-09T23:00:00Z" };
beforeEach(() => {
  vi.clearAllMocks();
  status = { state: "signed_out", message: "Connect your ChatGPT account to run AI analysis.", model: "test-model" };
  vi.mocked(adminAiConnection).mockImplementation(async () => ({ ...status }));
  vi.mocked(adminAiLogin).mockImplementation(async () => { status = { ...status, login }; return login; });
  vi.mocked(adminAiLoginCancel).mockImplementation(async () => { status = { ...status, login: { ...login, status: "cancelled", user_code: null, verification_url: null } }; });
});
afterEach(() => { cleanup(); vi.useRealTimers(); });

async function openConnection() {
  render(<AiConnection csrfToken="csrf-test" />);
  fireEvent.click(await screen.findByRole("button", { name: "AI connection: Connect AI" }));
  await screen.findByRole("button", { name: "Connect ChatGPT" });
}

it("starts a device-code login with CSRF and lets the user open ChatGPT and cancel", async () => {
  await openConnection();
  fireEvent.click(screen.getByRole("button", { name: "Connect ChatGPT" }));
  const code = await screen.findByLabelText("Enter this code in ChatGPT");
  expect(screen.queryByRole("combobox")).toBeNull();
  expect((code as HTMLInputElement).value).toBe("ABCD-1234");
  expect(screen.getByRole("link", { name: "Open ChatGPT" }).getAttribute("href")).toBe("https://auth.openai.com/codex/device");
  expect(adminAiLogin).toHaveBeenCalledWith({ headers: { "X-CSRF-Token": "csrf-test" } });
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  await waitFor(() => expect(adminAiLoginCancel).toHaveBeenCalledWith({ login_id: "test-login" }, { headers: { "X-CSRF-Token": "csrf-test" } }));
  await waitFor(() => expect(screen.queryByLabelText("Enter this code in ChatGPT")).toBeNull());
});

it("detects login completion without another click and removes the used code", async () => {
  vi.useFakeTimers();
  render(<AiConnection csrfToken="csrf-test" />);
  await act(async () => {});
  fireEvent.click(screen.getByRole("button", { name: "AI connection: Connect AI" }));
  await act(async () => {});
  fireEvent.click(screen.getByRole("button", { name: "Connect ChatGPT" }));
  await act(async () => {});
  expect(screen.getByLabelText("Enter this code in ChatGPT")).toBeTruthy();
  status = { ...status, state: "connected", email: "account@example.com", message: "Codex is online.", login: { ...login, status: "completed", user_code: null, verification_url: null } };
  const checks = vi.mocked(adminAiConnection).mock.calls.length;
  await act(async () => { await vi.advanceTimersByTimeAsync(9999); });
  expect(adminAiConnection).toHaveBeenCalledTimes(checks);
  await act(async () => { await vi.advanceTimersByTimeAsync(1); });
  expect(adminAiConnection).toHaveBeenCalledTimes(checks + 1);
  expect(screen.getByText("ChatGPT connected.")).toBeTruthy();
  expect(screen.queryByLabelText("Enter this code in ChatGPT")).toBeNull();
  expect(screen.getByText("account@example.com")).toBeTruthy();
});

it("shows outages and recovers on polling", async () => {
  vi.useFakeTimers();
  status = { ...status, state: "unavailable", message: "Codex is not responding." };
  render(<AiConnection csrfToken="csrf-test" />);
  await act(async () => {});
  expect(screen.getByRole("button", { name: "AI connection: AI unavailable" })).toBeTruthy();
  status = { ...status, state: "connected", message: "Codex is online." };
  await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
  expect(screen.getByRole("button", { name: "AI connection: AI connected" })).toBeTruthy();
  vi.mocked(adminAiConnection).mockRejectedValue(new Error("Network unavailable"));
  await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
  expect(screen.queryByRole("button", { name: "AI connection: AI connected" })).toBeNull();
  expect(screen.getByRole("button", { name: "AI connection: AI needs attention" })).toBeTruthy();
});

it("keeps failed sign-in feedback inline with a retry action", async () => {
  vi.mocked(adminAiLogin).mockRejectedValue(new ApiError(409, "Codex is not responding."));
  await openConnection();
  fireEvent.click(screen.getByRole("button", { name: "Connect ChatGPT" }));
  await screen.findByRole("alert");
  expect(screen.getByText("Codex is not responding.")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Connect ChatGPT" }).hasAttribute("disabled")).toBe(false);
});

it("shows the setup action when AI is disabled without offering sign-in", async () => {
  status = { state: "disabled", message: "AI analysis is disabled." };
  render(<AiConnection csrfToken="csrf-test" />);
  fireEvent.click(await screen.findByRole("button", { name: "AI connection: AI off" }));
  await screen.findByText("python3 scripts/compose_dev.py --ai");
  expect(screen.queryByRole("button", { name: "Connect ChatGPT" })).toBeNull();
});
