// @vitest-environment jsdom
import { StrictMode } from "react";
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { GooglePreferredSource } from "@/components/google-preferred-source";
import { trackEvent } from "@/lib/analytics";

let website = true;
vi.mock("@/lib/reader-runtime", () => ({ readerIsWebsite: () => website }));
vi.mock("@/lib/analytics", () => ({ trackEvent: vi.fn() }));

beforeEach(() => {
  website = true;
  document.documentElement.classList.remove("dark");
  vi.mocked(trackEvent).mockClear();
});
afterEach(() => {
  cleanup();
  document.documentElement.classList.remove("dark");
});

function widget() {
  return screen.getByTitle("Add DevFeed as a preferred source on Google") as HTMLIFrameElement;
}
function clicked(frame: HTMLIFrameElement, overrides: MessageEventInit = {}) {
  window.dispatchEvent(
    new MessageEvent("message", {
      source: frame.contentWindow,
      origin: new URL(frame.src).origin,
      data: { type: "devfeed:preferred-source-click" },
      ...overrides,
    }),
  );
}

it("isolates the real website widget and follows the resolved reader theme", async () => {
  render(<GooglePreferredSource />);
  const frame = widget();
  expect(frame.getAttribute("src")).toBe("/preferred-source/widget?theme=light");
  expect(frame.tabIndex).toBe(0);
  expect(frame.width).toBe("240");
  expect(frame.height).toBe("52");
  expect(document.querySelector("a[data-google-preferred-source]")).toBeNull();
  expect(document.querySelector('script[src*="news.google.com"]')).toBeNull();
  await act(async () => document.documentElement.classList.add("dark"));
  expect(widget().getAttribute("src")).toBe("/preferred-source/widget?theme=dark");
  expect(widget().style.colorScheme).toBe("dark");
  expect(trackEvent).not.toHaveBeenCalled();
});

it("tracks only click messages from the exact widget window and origin", () => {
  const view = render(<GooglePreferredSource />);
  const frame = widget();
  clicked(frame, { origin: "https://other.example" });
  clicked(frame, { source: window });
  clicked(frame, { data: { type: "preferred-source-added" } });
  clicked(frame, { data: null });
  expect(trackEvent).not.toHaveBeenCalled();
  clicked(frame);
  expect(trackEvent).toHaveBeenCalledExactlyOnceWith("preferred_source_button_click", {});
  expect(frame.getAttribute("title")).toBe("Add DevFeed as a preferred source on Google");
  view.unmount();
  clicked(frame);
  expect(trackEvent).toHaveBeenCalledOnce();
});

it("keeps one message subscription through Strict Mode and measures distinct clicks", () => {
  render(
    <StrictMode>
      <GooglePreferredSource />
    </StrictMode>,
  );
  clicked(widget());
  expect(trackEvent).toHaveBeenCalledOnce();
  clicked(widget());
  expect(trackEvent).toHaveBeenCalledTimes(2);
});

it("accepts only bounded widget sizes and resets the frame on a theme change", async () => {
  render(<GooglePreferredSource />);
  const frame = widget();
  for (const height of [0, 51, 401, 52.5, "252", null])
    clicked(frame, { data: { type: "devfeed:preferred-source-size", height } });
  clicked(frame, {
    origin: "https://other.example",
    data: { type: "devfeed:preferred-source-size", height: 252 },
  });
  clicked(frame, {
    source: window,
    data: { type: "devfeed:preferred-source-size", height: 252 },
  });
  expect(frame.height).toBe("52");
  await act(async () =>
    clicked(frame, {
      data: { type: "devfeed:preferred-source-size", height: 264 },
    }),
  );
  expect(frame.height).toBe("264");
  expect(frame.dataset.expanded).toBe("true");
  await act(async () => document.documentElement.classList.add("dark"));
  const darkFrame = widget();
  expect(darkFrame).not.toBe(frame);
  expect(darkFrame.height).toBe("52");
  clicked(frame, { data: { type: "devfeed:preferred-source-size", height: 400 } });
  expect(darkFrame.height).toBe("52");
  await act(async () =>
    clicked(darkFrame, {
      data: { type: "devfeed:preferred-source-size", height: 400 },
    }),
  );
  expect(darkFrame.height).toBe("400");
  await act(async () =>
    clicked(darkFrame, {
      data: { type: "devfeed:preferred-source-size", height: 52 },
    }),
  );
  expect(darkFrame.height).toBe("52");
  await act(async () => document.documentElement.classList.remove("dark"));
  expect(widget().height).toBe("52");
  expect(trackEvent).not.toHaveBeenCalled();
});

it("uses packaged official artwork and Google's documented selection link in extensions", async () => {
  website = false;
  render(<GooglePreferredSource />);
  const link = screen.getByRole("link", {
    name: "Add DevFeed as a preferred source on Google (opens in a new tab)",
  });
  expect(link.getAttribute("href")).toBe(
    "https://www.google.com/preferences/source?q=devfeed.tech",
  );
  expect(link.getAttribute("target")).toBe("_blank");
  expect(link.getAttribute("rel")).toBe("noopener noreferrer");
  expect(link.hasAttribute("data-google-preferred-source")).toBe(true);
  expect(link.hasAttribute("aria-pressed")).toBe(false);
  const image = screen.getByRole("img", { name: "Add DevFeed as a preferred source on Google" });
  expect(image.getAttribute("src")).toBe("/google-preferred-source/light.webp");
  await act(async () => document.documentElement.classList.add("dark"));
  expect(image.getAttribute("src")).toBe("/google-preferred-source/dark.webp");
  expect(document.querySelector("iframe")).toBeNull();
  expect(trackEvent).not.toHaveBeenCalled();
});
