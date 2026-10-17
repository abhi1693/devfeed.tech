// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
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
afterEach(() => vi.unstubAllGlobals());
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

it("skips optional telemetry entirely when Web Crypto cannot create a session", async () => {
  vi.stubGlobal("crypto", undefined);
  expect(await initializeBrowserTelemetry(settings)).toBeUndefined();
  expect(mocks.initialize).not.toHaveBeenCalled();
  expect(mocks.pushEvent).not.toHaveBeenCalled();
  expect(mocks.tracing).not.toHaveBeenCalled();
});

it("keeps telemetry disabled after crypto fails, even if secure randomness later recovers", async () => {
  await initializeBrowserTelemetry(settings);
  const config = mocks.initialize.mock.calls[0][0];
  expect(config.sessionTracking.generateSessionId()).toMatch(/^[0-9a-f-]{36}$/);
  vi.stubGlobal("crypto", undefined);
  expect(config.sessionTracking.generateSessionId()).toBe("");
  expect(config.beforeSend({ type: "event", payload: { name: "route_change" } })).toBeNull();
  vi.stubGlobal("crypto", { randomUUID: () => "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee" });
  expect(config.sessionTracking.generateSessionId()).toBe("");
  expect(config.beforeSend({ type: "event", payload: { name: "route_change" } })).toBeNull();
  config.sessionTracking.onSessionChange(null, { attributes: { isSampled: "true" } });
  await vi.dynamicImportSettled();
  expect(mocks.tracing).not.toHaveBeenCalled();
});

it("does not add tracing when crypto fails while the tracing module is loading", async () => {
  let startImport!: () => void;
  let finishImport!: () => void;
  const started = new Promise<void>((resolve) => {
    startImport = resolve;
  });
  vi.doMock(
    "@grafana/faro-web-tracing",
    () =>
      new Promise((resolve) => {
        finishImport = () => resolve({ TracingInstrumentation: mocks.tracing });
        startImport();
      }),
  );
  try {
    await initializeBrowserTelemetry(settings);
    const config = mocks.initialize.mock.calls[0][0];
    config.sessionTracking.onSessionChange(null, { attributes: { isSampled: "true" } });
    await started;
    vi.stubGlobal("crypto", undefined);
    expect(config.sessionTracking.generateSessionId()).toBe("");
    finishImport();
    await vi.dynamicImportSettled();
    expect(mocks.tracing).not.toHaveBeenCalled();
    expect(mocks.add).not.toHaveBeenCalled();
  } finally {
    finishImport?.();
    vi.doMock("@grafana/faro-web-tracing", () => ({ TracingInstrumentation: mocks.tracing }));
  }
});
