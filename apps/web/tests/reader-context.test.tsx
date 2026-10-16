// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, expect, it } from "vitest";
import {
  ReaderContextProvider,
  ReaderSurface,
  useReaderContext,
} from "@/components/reader-context";
import { readerPromptPolicies } from "@/lib/reader-context";
import { ReaderNotFound } from "@/components/reader-not-found";

afterEach(() => {
  cleanup();
  window.history.replaceState({}, "", "/");
});
function Probe() {
  return <output data-testid="reader-context">{JSON.stringify(useReaderContext())}</output>;
}
const state = () => JSON.parse(screen.getByTestId("reader-context").textContent!);

it("denies automatic prompts without a rendered surface, independent of the URL", () => {
  window.history.replaceState({}, "", "/articles/looks-valid");
  const view = render(
    <ReaderContextProvider>
      <Probe />
    </ReaderContextProvider>,
  );
  expect(state()).toEqual({ currentArticle: null, promptEligibility: readerPromptPolicies.quiet });
  window.history.replaceState({}, "", "/latest");
  view.rerender(
    <ReaderContextProvider>
      <Probe />
    </ReaderContextProvider>,
  );
  expect(state()).toEqual({ currentArticle: null, promptEligibility: readerPromptPolicies.quiet });
});

it("keeps foreground ownership when background policy mounts later", () => {
  const article = { id: "loaded-id", slug: "loaded-article" };
  const tree = (active: boolean, quietPage: boolean) => (
    <ReaderContextProvider>
      <ReaderSurface promptEligibility={readerPromptPolicies.browsing} />
      <ReaderSurface
        priority="foreground"
        currentArticle={article}
        promptEligibility={readerPromptPolicies.quiet}
        active={active}
      />
      {quietPage && <ReaderNotFound />}
      <Probe />
    </ReaderContextProvider>
  );
  const view = render(tree(true, false));
  view.rerender(tree(true, true));
  expect(state()).toEqual({
    currentArticle: article,
    promptEligibility: readerPromptPolicies.quiet,
  });
  view.rerender(tree(false, true));
  expect(state()).toEqual({ currentArticle: null, promptEligibility: readerPromptPolicies.quiet });
  view.rerender(tree(false, false));
  expect(state()).toEqual({
    currentArticle: null,
    promptEligibility: readerPromptPolicies.browsing,
  });
});

it("releasing an older foreground cannot remove the newer owner's identity", () => {
  const tree = (older: boolean) => (
    <ReaderContextProvider>
      {older && (
        <ReaderSurface
          key="older"
          priority="foreground"
          currentArticle={{ id: "old", slug: "old" }}
          promptEligibility={readerPromptPolicies.quiet}
        />
      )}
      <ReaderSurface
        key="newer"
        priority="foreground"
        currentArticle={{ id: "new", slug: "new" }}
        promptEligibility={readerPromptPolicies.quiet}
      />
      <Probe />
    </ReaderContextProvider>
  );
  const view = render(tree(true));
  expect(state().currentArticle).toEqual({ id: "new", slug: "new" });
  view.rerender(tree(false));
  expect(state().currentArticle).toEqual({ id: "new", slug: "new" });
});

it("releases StrictMode registrations when a surface becomes inactive", () => {
  const tree = (active: boolean) => (
    <StrictMode>
      <ReaderContextProvider>
        <ReaderSurface
          currentArticle={{ id: "article", slug: "article" }}
          promptEligibility={readerPromptPolicies.quiet}
          active={active}
        />
        <Probe />
      </ReaderContextProvider>
    </StrictMode>
  );
  const view = render(tree(true));
  expect(state().currentArticle).toEqual({ id: "article", slug: "article" });
  view.rerender(tree(false));
  expect(state()).toEqual({ currentArticle: null, promptEligibility: readerPromptPolicies.quiet });
});
