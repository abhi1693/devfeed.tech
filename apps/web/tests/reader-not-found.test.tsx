import { expect, it } from "vitest";
import NotFound from "@/app/not-found";
import ReaderLink from "@/components/reader-link";
import { ReaderNotFound } from "@/components/reader-not-found";

it("shares the reader missing page and its runtime-aware recovery link", () => {
  expect(NotFound().type).toBe(ReaderNotFound);
  const page = ReaderNotFound();
  expect(page.type).toBe("main");
  expect(page.props.className).toBe("standalone-state");
  expect(page.props.children[1].props.children).toBe("Page not found");
  const recovery = page.props.children.at(-1);
  expect(recovery.type).toBe(ReaderLink);
  expect(recovery.props).toMatchObject({ href: "/", children: "Discover something else" });
});
