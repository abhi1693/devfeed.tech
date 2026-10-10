// @vitest-environment jsdom
import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { ProfileAvatar } from "@/components/profile-avatar";

afterEach(cleanup);
it("keeps initials on failures and recovers when the selected avatar changes or is removed", () => {
  const { container, rerender } = render(
    <ProfileAvatar name="Reader Person" url="https://avatars.githubusercontent.com/u/123?v=4" />,
  );
  fireEvent.error(container.querySelector("img")!);
  expect(container.querySelector("img")).toBeNull();
  expect(container.textContent).toBe("RP");
  rerender(<ProfileAvatar name="Reader Person" url="https://publisher.test/new.png" />);
  expect(container.querySelector("img")?.getAttribute("src")).toBe(
    "https://publisher.test/new.png",
  );
  rerender(<ProfileAvatar name="Reader Person" url={null} />);
  expect(container.querySelector("img")).toBeNull();
  expect(container.textContent).toBe("RP");
});
