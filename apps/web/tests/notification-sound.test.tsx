// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, renderHook } from "@testing-library/react";
import { useNotificationSound } from "@devfeed/ui/use-notification-sound";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });
it("plays only for new arrivals after interaction and closes audio when disabled", () => {
  vi.spyOn(Date, "now").mockReturnValue(1000);
  const oscillator = { frequency: { value: 0 }, connect: vi.fn(), disconnect: vi.fn(), start: vi.fn(), stop: vi.fn() };
  const audio = { state: "running", currentTime: 0, destination: {}, resume: vi.fn().mockResolvedValue(undefined), close: vi.fn().mockResolvedValue(undefined), createOscillator: vi.fn(() => oscillator), createGain: () => ({ gain: { setValueAtTime: vi.fn(), exponentialRampToValueAtTime: vi.fn() }, connect: vi.fn(), disconnect: vi.fn() }) };
  vi.stubGlobal("AudioContext", class { constructor() { return audio; } });
  const hook = renderHook(({ count, loading, enabled, newest }) => useNotificationSound(count, loading, enabled, newest), { initialProps: { count: 1, loading: false, enabled: true, newest: 900 } });
  expect(audio.createOscillator).not.toHaveBeenCalled();
  fireEvent.pointerDown(window);
  hook.rerender({ count: 2, loading: false, enabled: true, newest: 1100 });
  expect(oscillator.start).toHaveBeenCalledOnce();
  hook.rerender({ count: 3, loading: false, enabled: true, newest: 900 });
  expect(oscillator.start).toHaveBeenCalledOnce();
  hook.rerender({ count: 4, loading: false, enabled: false, newest: 1200 });
  expect(oscillator.start).toHaveBeenCalledOnce();expect(audio.close).toHaveBeenCalledOnce();
});
