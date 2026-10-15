// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { createContext, useContext, useEffect, useState } from "react";
import { renderToString } from "react-dom/server";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  HomepageSponsorship,
  HomepageSponsorshipProvider,
  HomepageSponsorshipSlot,
  useHomepageSponsorship,
} from "@/components/homepage-sponsorship";

let media: EventTarget & { matches: boolean };
beforeEach(() => {
  media = Object.assign(new EventTarget(), { matches: true });
  vi.stubGlobal(
    "matchMedia",
    vi.fn(() => media),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function Readiness() {
  const { ready, available } = useHomepageSponsorship();
  return <output>{`${ready}:${available}`}</output>;
}

it("waits for the client viewport before offering homepage sponsorship", () => {
  const content = (
    <HomepageSponsorshipProvider>
      <Readiness />
    </HomepageSponsorshipProvider>
  );
  expect(renderToString(content)).toContain("false:false");
  expect(window.matchMedia).not.toHaveBeenCalled();
  render(content);
  expect(screen.getByText("true:true")).toBeDefined();
  expect(window.matchMedia).toHaveBeenCalledWith("(min-width: 1280px)");
});

it("renders a live creative in the registered header while preserving its React context", () => {
  const Account = createContext("wrong account");
  function Creative() {
    const account = useContext(Account);
    const [clicks, setClicks] = useState(0);
    return <button onClick={() => setClicks(clicks + 1)}>{`${account}:${clicks}`}</button>;
  }
  const mounted = render(
    <HomepageSponsorshipProvider>
      <header data-testid="header">
        <HomepageSponsorshipSlot />
      </header>
      <main data-testid="feed">
        <Account.Provider value="Selected partner">
          <HomepageSponsorship>
            <Creative />
          </HomepageSponsorship>
        </Account.Provider>
      </main>
    </HomepageSponsorshipProvider>,
  );
  const creative = screen.getByRole("button", { name: "Selected partner:0" });
  expect(screen.getByTestId("header").contains(creative)).toBe(true);
  expect(screen.getByTestId("feed").contains(creative)).toBe(false);
  fireEvent.click(creative);
  expect(screen.getByRole("button", { name: "Selected partner:1" })).toBe(creative);
  mounted.unmount();
  expect(creative.isConnected).toBe(false);
});

it("mounts visible creative only on wide viewports and cleans up observers when narrowing", () => {
  media.matches = false;
  const started = vi.fn();
  const stopped = vi.fn();
  function Creative() {
    useEffect(() => {
      started();
      return stopped;
    }, []);
    return <a href="https://partner.example">Homepage sponsor</a>;
  }
  const removed = vi.spyOn(media, "removeEventListener");
  const added = vi.spyOn(media, "addEventListener");
  const mounted = render(
    <HomepageSponsorshipProvider>
      <Readiness />
      <HomepageSponsorshipSlot />
      <HomepageSponsorship>
        <Creative />
      </HomepageSponsorship>
    </HomepageSponsorshipProvider>,
  );
  expect(screen.getByText("true:false")).toBeDefined();
  expect(screen.queryByRole("link")).toBeNull();
  expect(started).not.toHaveBeenCalled();
  act(() => {
    media.matches = true;
    media.dispatchEvent(new Event("change"));
  });
  expect(screen.getByRole("link", { name: "Homepage sponsor" })).toBeDefined();
  expect(started).toHaveBeenCalledTimes(1);
  act(() => {
    media.matches = false;
    media.dispatchEvent(new Event("change"));
  });
  expect(screen.queryByRole("link")).toBeNull();
  expect(stopped).toHaveBeenCalledTimes(1);
  act(() => {
    media.matches = true;
    media.dispatchEvent(new Event("change"));
  });
  expect(started).toHaveBeenCalledTimes(2);
  mounted.unmount();
  expect(stopped).toHaveBeenCalledTimes(2);
  expect(removed).toHaveBeenCalledWith("change", added.mock.calls[0][1]);
});

it("does not render an unregistered sponsor or leave it behind when its feed unmounts", () => {
  const content = (slot: boolean, feed: boolean) => (
    <HomepageSponsorshipProvider>
      {slot && <HomepageSponsorshipSlot />}
      {feed && (
        <HomepageSponsorship>
          <a href="https://partner.example">Homepage sponsor</a>
        </HomepageSponsorship>
      )}
    </HomepageSponsorshipProvider>
  );
  const mounted = render(content(false, true));
  expect(screen.queryByRole("link")).toBeNull();
  mounted.rerender(content(true, true));
  expect(screen.getByRole("link", { name: "Homepage sponsor" })).toBeDefined();
  mounted.rerender(content(true, false));
  expect(screen.queryByRole("link")).toBeNull();
  expect(mounted.container.querySelector(".topbar-sponsorship")?.childElementCount).toBe(0);
});
