import { expect, it } from "vitest";
import ReaderLink from "@/components/reader-link";

it("disables prefetch while preserving navigation props and refs", () => {
  const ref = { current: null };
  const onClick = () => {};
  const link = ReaderLink({
    href: "/topics/kubernetes",
    prefetch: true,
    scroll: false,
    ref,
    onClick,
  });
  expect(link.props).toMatchObject({
    href: "/topics/kubernetes",
    prefetch: false,
    scroll: false,
    ref,
    onClick,
  });
});
