// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render } from "@testing-library/react";
import type { ComponentProps } from "react";
import type NextLink from "next/link";
import ReaderLink from "@/components/reader-link";
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
afterEach(cleanup);

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
