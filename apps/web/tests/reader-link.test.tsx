// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render } from "@testing-library/react";
import type { ComponentProps } from "react";
import type NextLink from "next/link";
import ReaderLink from "@/components/reader-link";
import { articleDestination } from "@/lib/reader-destination";
import { readerNavigate } from "@/lib/reader-runtime";
import { cancelDialogScrollPreparation } from "@/lib/dialog-scroll";
const capture = vi.hoisted(() => ({ props: {} as ComponentProps<typeof NextLink> }));
vi.mock("next/link", () => ({
  default: (props: ComponentProps<typeof NextLink>) => {
    capture.props = props;
    return (
      <a href={String(props.href)} ref={props.ref}>
        {props.children}
      </a>
    );
  },
}));
vi.mock("@/lib/reader-runtime", async (original) => ({
  ...(await original<typeof import("@/lib/reader-runtime")>()),
  readerNavigate: vi.fn(() => false),
}));
beforeEach(() => vi.spyOn(window, "scrollTo").mockImplementation(() => {}));
afterEach(() => {
  cleanup();
  cancelDialogScrollPreparation();
  vi.mocked(readerNavigate).mockReset().mockReturnValue(false);
  vi.restoreAllMocks();
});

it("disables prefetch while preserving navigation props and refs", () => {
  const ref = { current: null };
  const onClick = vi.fn();
  render(
    <ReaderLink
      {...{
        href: "/topics/kubernetes",
        prefetch: true,
        scroll: false,
        ref,
        onClick,
      }}
    />,
  );
  expect(capture.props).toMatchObject({
    href: "/topics/kubernetes",
    prefetch: false,
    scroll: false,
    ref,
  });
  const event = { currentTarget: { href: "https://reader.test/topics/kubernetes" } };
  capture.props.onClick!(
    event as unknown as Parameters<NonNullable<typeof capture.props.onClick>>[0],
  );
  expect(onClick).toHaveBeenCalledWith(event);
});

it("starts recovery only for an accepted client navigation", () => {
  const started = vi.fn();
  window.addEventListener("devfeed:reader-navigation", started);
  try {
    const view = render(<ReaderLink href="/sources" />);
    capture.props.onNavigate!({ preventDefault: vi.fn() });
    expect(started).toHaveBeenCalledOnce();
    expect(started.mock.calls[0][0].detail).toBe("/sources");
    started.mockClear();
    view.rerender(<ReaderLink href="/sources" onNavigate={(event) => event.preventDefault()} />);
    const preventDefault = vi.fn();
    capture.props.onNavigate!({ preventDefault });
    expect(preventDefault).toHaveBeenCalledOnce();
    expect(started).not.toHaveBeenCalled();
  } finally {
    window.removeEventListener("devfeed:reader-navigation", started);
  }
});

it("serializes typed article links and preserves their declared scroll policy", () => {
  const destination = articleDestination("docker-image-guide");
  render(<ReaderLink href={destination} scroll />);
  expect(capture.props).toMatchObject({
    href: destination.href,
    scroll: false,
    prefetch: false,
  });
  const preventDefault = vi.fn();
  capture.props.onNavigate!({ preventDefault });
  expect(readerNavigate).toHaveBeenCalledWith(destination, "push");
  expect(preventDefault).not.toHaveBeenCalled();
});

it("passes typed destinations directly to the bundled reader adapter", () => {
  vi.mocked(readerNavigate).mockReturnValue(true);
  const destination = articleDestination("docker-image-guide");
  const view = render(<ReaderLink href={destination} replace />);
  const preventDefault = vi.fn();
  capture.props.onNavigate!({ preventDefault });
  expect(readerNavigate).toHaveBeenCalledWith(destination, "replace");
  expect(preventDefault).toHaveBeenCalledOnce();
  vi.mocked(readerNavigate).mockClear();
  view.rerender(<ReaderLink href={destination} onNavigate={(event) => event.preventDefault()} />);
  capture.props.onNavigate!({ preventDefault: vi.fn() });
  expect(readerNavigate).not.toHaveBeenCalled();
});
