// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { TopicFollow } from "@/components/topic-follow";
import { UserProvider } from "@/components/user-account";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("offers optional sign-in returning to the article without writing preferences", async () => {
  const fetcher = vi.fn().mockResolvedValue(Response.json(null));
  vi.stubGlobal("fetch", fetcher);
  render(
    <UserProvider>
      <TopicFollow topicId="topic-a" articleId="article-a" />
    </UserProvider>,
  );
  const link = await screen.findByRole("link", { name: "Follow" });
  expect(link.getAttribute("href")).toBe(
    "/api/v1/user/auth/login?return_to=%2Farticles%2Farticle-a",
  );
  expect(fetcher).toHaveBeenCalledTimes(1);
});

it("changes only this topic and retains the saved state when a write fails", async () => {
  let fail = false;
  const fetcher = vi.fn((url: string, init?: RequestInit) =>
    Promise.resolve(
      url.endsWith("auth/me")
        ? Response.json({ user_id: "user-a", csrf_token: "csrf" })
        : init?.method === "PUT"
          ? fail
            ? Response.json({}, { status: 503 })
            : Response.json({ followed: true })
          : Response.json({ topic_ids: ["another-topic"] }),
    ),
  );
  vi.stubGlobal("fetch", fetcher);
  render(
    <UserProvider>
      <TopicFollow topicId="topic-a" articleId="article-a" />
    </UserProvider>,
  );
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Follow" })).toHaveProperty(
      "disabled",
      false,
    ),
  );
  fireEvent.click(screen.getByRole("button", { name: "Follow" }));
  await screen.findByRole("button", { name: "Following" });
  const [url, options] = fetcher.mock.calls.find(
    ([, init]) => init?.method === "PUT",
  )!;
  expect(url).toBe("/api/v1/user/preferences/topics/topic-a");
  expect(options?.body).toBe('{"followed":true}');
  expect(options?.headers).toEqual({
    "Content-Type": "application/json",
    "X-CSRF-Token": "csrf",
  });
  fail = true;
  fireEvent.click(screen.getByRole("button", { name: "Following" }));
  await screen.findByRole("alert");
  expect(
    screen
      .getByRole("button", { name: "Following" })
      .getAttribute("aria-pressed"),
  ).toBe("true");
});
