// @vitest-environment jsdom
import { beforeEach, expect, it, vi } from "vitest";
const mocks = vi.hoisted(() => ({
  initialize: vi.fn(),
  tracing: vi.fn(),
  add: vi.fn(),
  getSession: vi.fn(),
  setView: vi.fn(),
  pushEvent: vi.fn(),
}));
vi.mock("@grafana/faro-web-sdk", () => ({
  initializeFaro: mocks.initialize,
  getWebInstrumentations: () => [],
}));
vi.mock("@grafana/faro-web-tracing", () => ({ TracingInstrumentation: mocks.tracing }));
import { initializeBrowserTelemetry } from "../../../packages/telemetry/src/browser-initialize";
const settings = { enabled: true, app: "web" as const, version: "test", environment: "test" };
beforeEach(() => {
  vi.clearAllMocks();
  mocks.getSession.mockReturnValue({ attributes: { isSampled: "false" } });
  mocks.initialize.mockReturnValue({
    api: { getSession: mocks.getSession, setView: mocks.setView, pushEvent: mocks.pushEvent },
    instrumentations: { add: mocks.add },
  });
});
it("keeps error/event telemetry and privacy settings without tracing in unsampled sessions", async () => {
  await initializeBrowserTelemetry(settings);
  expect(mocks.tracing).not.toHaveBeenCalled();
  expect(mocks.pushEvent).toHaveBeenCalledWith("telemetry_ready");
  expect(mocks.initialize.mock.calls[0][0]).toMatchObject({
    preventGlobalExposure: true,
    requestCompression: false,
    trackResources: false,
    sessionTracking: { samplingRate: 0.1, persistent: false },
    beforeSend: expect.any(Function),
  });
});
it("starts tracing once for sampled sessions, including later session changes", async () => {
  await initializeBrowserTelemetry(settings);
  const change = mocks.initialize.mock.calls[0][0].sessionTracking.onSessionChange;
  change(null, { attributes: { isSampled: "true" } });
  change(null, { attributes: { isSampled: "true" } });
  await vi.waitFor(() => expect(mocks.add).toHaveBeenCalledOnce());
  expect(mocks.tracing).toHaveBeenCalledWith(
    expect.objectContaining({ omitTraceContextForUnsampledSessions: true }),
  );
});
it("initializes tracing when the first session is already sampled", async () => {
  mocks.getSession.mockReturnValue({ attributes: { isSampled: "true" } });
  await initializeBrowserTelemetry(settings);
  await vi.waitFor(() => expect(mocks.add).toHaveBeenCalledOnce());
});
it("allows another sampled session to retry a failed tracing import/initialization", async () => {
  mocks.tracing.mockImplementationOnce(function () {
    throw new Error("blocked");
  });
  mocks.getSession.mockReturnValue({ attributes: { isSampled: "true" } });
  await initializeBrowserTelemetry(settings);
  await vi.waitFor(() => expect(mocks.tracing).toHaveBeenCalledOnce());
  await new Promise((resolve) => setTimeout(resolve, 0));
  mocks.initialize.mock.calls[0][0].sessionTracking.onSessionChange(null, {
    attributes: { isSampled: "true" },
  });
  await vi.waitFor(() => expect(mocks.add).toHaveBeenCalledOnce());
});
