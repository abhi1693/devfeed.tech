// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SourceSuggestion, SuggestSourceLink } from "@/components/source-suggestion";
import { UserProvider } from "@/components/user-account";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
const validReceipt = {
  id: "11111111-1111-4111-8111-111111111111",
  name: "Reader blog",
  approval_status: "pending",
  created_at: "2026-10-15T00:00:00Z",
};
function setup(signedIn = true, status = 201, receipt: unknown = validReceipt) {
  const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith("auth/me"))
      return Response.json(
        signedIn ? { user_id: "one", name: "Reader", csrf_token: "csrf" } : null,
      );
    if (url.endsWith("suggestions/preview")) return Response.json({ name: "Feed title" });
    if (init?.method === "POST") return Response.json(status === 201 ? receipt : {}, { status });
    return Response.json({});
  });
  vi.stubGlobal("fetch", fetcher);
  return fetcher;
}
it("requires sign-in and returns to source suggestions after login", async () => {
  const fetcher = setup(false);
  render(
    <UserProvider>
      <SuggestSourceLink />
      <SourceSuggestion />
    </UserProvider>,
  );
  await screen.findByRole("heading", { name: "Sign in to suggest a source" });
  expect(screen.queryByRole("button", { name: "Submit suggestion" })).toBeNull();
  expect(screen.getByRole("link", { name: "Suggest a source" }).getAttribute("href")).toContain(
    encodeURIComponent("/sources/suggest"),
  );
  expect(fetcher.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
});
it("sends only suggestion fields with CSRF and explains pending review", async () => {
  const fetcher = setup();
  render(
    <UserProvider>
      <SourceSuggestion />
    </UserProvider>,
  );
  fireEvent.change(await screen.findByLabelText("Feed or YouTube URL"), {
    target: { value: "https://example.com/rss" },
  });
  fireEvent.change(screen.getByLabelText("Name (optional)"), {
    target: { value: " Reader blog " },
  });
  await waitFor(
    () =>
      expect(
        (screen.getByRole("button", { name: "Submit suggestion" }) as HTMLButtonElement).disabled,
      ).toBe(false),
    { timeout: 2000 },
  );
  fireEvent.click(screen.getByRole("button", { name: "Submit suggestion" }));
  await screen.findByRole("heading", { name: "Suggestion received" });
  expect(screen.getByText(/Pending review/)).toBeTruthy();
  expect(screen.getByText("Reader blog")).toBeTruthy();
  expect(document.activeElement?.textContent).toBe("Suggestion received");
  const post = fetcher.mock.calls.find(
    ([url, init]) => init?.method === "POST" && !url.endsWith("preview"),
  )!;
  expect(post[0]).toBe("/api/v1/user/sources/suggestions");
  expect(post[1]?.headers).toEqual({ "Content-Type": "application/json", "X-CSRF-Token": "csrf" });
  expect(JSON.parse(String(post[1]?.body))).toEqual({
    feed_url: "https://example.com/rss",
    name: "Reader blog",
    source_type: "publisher",
  });
});
it.each([409, 422, 429, 503])("retains entered details after a %s response", async (status) => {
  setup(true, status);
  render(
    <UserProvider>
      <SourceSuggestion />
    </UserProvider>,
  );
  const input = await screen.findByLabelText("Feed or YouTube URL");
  fireEvent.change(input, { target: { value: "https://example.com/rss" } });
  await waitFor(
    () =>
      expect(
        (screen.getByRole("button", { name: "Submit suggestion" }) as HTMLButtonElement).disabled,
      ).toBe(false),
    { timeout: 2000 },
  );
  fireEvent.click(screen.getByRole("button", { name: "Submit suggestion" }));
  await screen.findByRole("alert");
  expect((input as HTMLInputElement).value).toBe("https://example.com/rss");
  await waitFor(() =>
    expect(
      (screen.getByRole("button", { name: "Submit suggestion" }) as HTMLButtonElement).disabled,
    ).toBe(false),
  );
});

it("fills the feed name before submission and lets the reader edit it", async () => {
  const fetcher = setup();
  render(
    <UserProvider>
      <SourceSuggestion />
    </UserProvider>,
  );
  fireEvent.change(await screen.findByLabelText("Feed or YouTube URL"), {
    target: { value: "https://example.com/rss" },
  });
  await waitFor(
    () =>
      expect((screen.getByLabelText("Name (optional)") as HTMLInputElement).value).toBe(
        "Feed title",
      ),
    { timeout: 2000 },
  );
  expect(fetcher.mock.calls.filter(([url]) => url.endsWith("sources/suggestions"))).toHaveLength(0);
  fireEvent.change(screen.getByLabelText("Name (optional)"), {
    target: { value: "My edited name" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Submit suggestion" }));
  await screen.findByRole("heading", { name: "Suggestion received" });
  const post = fetcher.mock.calls.find(([url]) => url.endsWith("sources/suggestions"))!;
  expect(JSON.parse(String(post[1]?.body)).name).toBe("My edited name");
});
it("does not replace a manually edited name when the lookup finishes", async () => {
  setup();
  render(
    <UserProvider>
      <SourceSuggestion />
    </UserProvider>,
  );
  fireEvent.change(await screen.findByLabelText("Feed or YouTube URL"), {
    target: { value: "https://example.com/rss" },
  });
  fireEvent.change(screen.getByLabelText("Name (optional)"), {
    target: { value: "Keep this name" },
  });
  await waitFor(() => expect(screen.queryByText("Looking up the feed name…")).toBeNull(), {
    timeout: 2000,
  });
  expect((screen.getByLabelText("Name (optional)") as HTMLInputElement).value).toBe(
    "Keep this name",
  );
});

it("aborts a pending submission on unmount and emits no late success event", async () => {
  let finish!: (response: Response) => void;
  let submissionSignal: AbortSignal | null | undefined;
  const analytics = vi.fn();
  window.addEventListener("devfeed:analytics", analytics);
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, init?: RequestInit) => {
      if (url.endsWith("auth/me"))
        return Promise.resolve(Response.json({ user_id: "one", csrf_token: "csrf" }));
      if (url.endsWith("preview")) return Promise.resolve(Response.json({ name: "Feed title" }));
      submissionSignal = init?.signal;
      return new Promise<Response>((resolve) => {
        finish = resolve;
      });
    }),
  );
  try {
    const view = render(
      <UserProvider>
        <SourceSuggestion />
      </UserProvider>,
    );
    fireEvent.change(await screen.findByLabelText("Feed or YouTube URL"), {
      target: { value: "https://example.com/rss" },
    });
    await waitFor(
      () =>
        expect(
          (screen.getByRole("button", { name: "Submit suggestion" }) as HTMLButtonElement).disabled,
        ).toBe(false),
      { timeout: 2000 },
    );
    fireEvent.click(screen.getByRole("button", { name: "Submit suggestion" }));
    expect(submissionSignal?.aborted).toBe(false);
    view.unmount();
    expect(submissionSignal?.aborted).toBe(true);
    await act(async () =>
      finish(Response.json({ ...validReceipt, name: "Late feed" }, { status: 201 })),
    );
    expect(analytics).not.toHaveBeenCalled();
  } finally {
    window.removeEventListener("devfeed:analytics", analytics);
  }
});

it("retains the entered source and emits no success event for a malformed receipt", async () => {
  setup(true, 201, { name: "Reader blog", approval_status: "pending" });
  const analytics = vi.fn();
  window.addEventListener("devfeed:analytics", analytics);
  try {
    render(
      <UserProvider>
        <SourceSuggestion />
      </UserProvider>,
    );
    const input = await screen.findByLabelText("Feed or YouTube URL");
    fireEvent.change(input, { target: { value: "https://example.com/rss" } });
    await waitFor(
      () =>
        expect(
          (screen.getByRole("button", { name: "Submit suggestion" }) as HTMLButtonElement).disabled,
        ).toBe(false),
      { timeout: 2000 },
    );
    fireEvent.click(screen.getByRole("button", { name: "Submit suggestion" }));
    await screen.findByRole("alert");
    expect((input as HTMLInputElement).value).toBe("https://example.com/rss");
    expect(screen.queryByRole("heading", { name: "Suggestion received" })).toBeNull();
    expect(analytics).not.toHaveBeenCalled();
  } finally {
    window.removeEventListener("devfeed:analytics", analytics);
  }
});

it("previews both YouTube resources and submits only the explicitly selected target", async () => {
  const url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLDeveloperVideos";
  const fetcher = vi.fn(async (path: string, init?: RequestInit) => {
    if (path.endsWith("auth/me"))
      return Response.json({ user_id: "one", name: "Reader", csrf_token: "csrf" });
    if (path.endsWith("suggestions/preview")) {
      const target = JSON.parse(String(init?.body)).youtube_target ?? "video";
      return Response.json({
        name: target === "playlist" ? "Developer playlist" : "Developer video",
        provider: "youtube",
        resource_kind: target,
        canonical_url: url,
        resources: [
          { target: "video", canonical_url: "https://www.youtube.com/watch?v=dQw4w9WgXcQ" },
          {
            target: "playlist",
            canonical_url: "https://www.youtube.com/playlist?list=PLDeveloperVideos",
          },
        ],
      });
    }
    if (init?.method === "POST")
      return Response.json({ ...validReceipt, name: "Developer playlist" }, { status: 201 });
    return Response.json({});
  });
  vi.stubGlobal("fetch", fetcher);
  render(
    <UserProvider>
      <SourceSuggestion />
    </UserProvider>,
  );
  fireEvent.change(await screen.findByLabelText("Feed or YouTube URL"), { target: { value: url } });
  const selector = await screen.findByRole(
    "combobox",
    { name: "YouTube target" },
    { timeout: 2000 },
  );
  expect(selector.textContent).toContain("Only this video");
  fireEvent.click(selector);
  fireEvent.click(screen.getByRole("option", { name: "Entire playlist" }));
  await waitFor(
    () =>
      expect((screen.getByLabelText("Name (optional)") as HTMLInputElement).value).toBe(
        "Developer playlist",
      ),
    { timeout: 2000 },
  );
  fireEvent.click(screen.getByRole("button", { name: "Submit suggestion" }));
  await screen.findByRole("heading", { name: "Suggestion received" });
  const request = fetcher.mock.calls.find(
    ([path, init]) => init?.method === "POST" && path.endsWith("suggestions"),
  );
  expect(JSON.parse(String(request?.[1]?.body))).toMatchObject({
    feed_url: url,
    youtube_target: "playlist",
  });
  expect(fetcher.mock.calls.filter(([path]) => path.endsWith("preview"))).toHaveLength(2);
});

it("previews and submits a plain YouTube handle without native URL validation blocking it", async () => {
  const fetcher = setup();
  render(
    <UserProvider>
      <SourceSuggestion />
    </UserProvider>,
  );
  const input = (await screen.findByLabelText("Feed or YouTube URL")) as HTMLInputElement;
  fireEvent.change(input, { target: { value: "@developers" } });
  await waitFor(
    () =>
      expect(
        (screen.getByRole("button", { name: "Submit suggestion" }) as HTMLButtonElement).disabled,
      ).toBe(false),
    { timeout: 2000 },
  );
  expect(input.checkValidity()).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Submit suggestion" }));
  await screen.findByRole("heading", { name: "Suggestion received" });
  expect(screen.getByText("YouTube", { exact: true })).toBeTruthy();
  const post = fetcher.mock.calls.find(([url]) => url.endsWith("sources/suggestions"))!;
  expect(JSON.parse(String(post[1]?.body)).feed_url).toBe("@developers");
});
