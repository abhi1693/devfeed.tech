// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { TopicPreferences } from "@/components/topic-preferences";
import { UserProvider } from "@/components/user-account";
import { topic } from "./fixtures";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
it("toggles topics with the keyboard and saves the selection without checkboxes", async () => {
  const fetcher = vi.fn((url: string, init?: RequestInit) =>
    Promise.resolve(
      Response.json(
        url.endsWith("auth/me")
          ? { user_id: "user-a", csrf_token: "csrf" }
          : init?.method === "PUT"
            ? { topic_ids: [topic.id] }
            : { topic_ids: [] },
      ),
    ),
  );
  vi.stubGlobal("fetch", fetcher);
  const user = userEvent.setup();
  render(
    <UserProvider>
      <TopicPreferences topics={[topic]} />
    </UserProvider>,
  );
  const tile = await screen.findByRole("button", { name: topic.name });
  expect(screen.queryByRole("checkbox")).toBeNull();
  expect(tile.getAttribute("aria-pressed")).toBe("false");
  tile.focus();
  await user.keyboard(" ");
  expect(tile.getAttribute("aria-pressed")).toBe("true");
  await user.keyboard("{Enter}");
  expect(tile.getAttribute("aria-pressed")).toBe("false");
  await user.click(tile);
  await user.click(screen.getByRole("button", { name: "Save topics" }));
  await screen.findByText("Your topics are saved.");
  expect(
    JSON.parse(String(fetcher.mock.calls.find(([, init]) => init?.method === "PUT")?.[1]?.body)),
  ).toEqual({ topic_ids: [topic.id] });
});
