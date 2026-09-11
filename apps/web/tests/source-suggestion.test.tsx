// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SourceSuggestion, SuggestSourceLink } from "@/components/source-suggestion";
import { UserProvider } from "@/components/user-account";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
function setup(signedIn = true, status = 201) {
  const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith("auth/me")) return Response.json(signedIn ? { user_id: "one", name: "Reader", csrf_token: "csrf" } : null);
    if (url.endsWith("suggestions/preview")) return Response.json({ name: "Feed title" });
    if (init?.method === "POST") return Response.json(status === 201 ? { name: "Reader blog", approval_status: "pending" } : {}, { status });
    return Response.json({});
  });
  vi.stubGlobal("fetch", fetcher);
  return fetcher;
}
it("requires sign-in and returns to source suggestions after login", async () => {
  const fetcher = setup(false);
  render(<UserProvider><SuggestSourceLink /><SourceSuggestion /></UserProvider>);
  await screen.findByRole("heading", { name: "Sign in to suggest a source" });
  expect(screen.queryByRole("button", { name: "Submit suggestion" })).toBeNull();
  expect(screen.getByRole("link", { name: "Suggest a source" }).getAttribute("href")).toContain(encodeURIComponent("/sources/suggest"));
  expect(fetcher.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
});
it("sends only suggestion fields with CSRF and explains pending review", async () => {
  const fetcher = setup();
  render(<UserProvider><SourceSuggestion /></UserProvider>);
  fireEvent.change(await screen.findByLabelText("RSS or Atom URL"), { target: { value: "https://example.com/rss" } });
  fireEvent.change(screen.getByLabelText("Name (optional)"), { target: { value: " Reader blog " } });
  await waitFor(() => expect((screen.getByRole("button", { name: "Submit suggestion" }) as HTMLButtonElement).disabled).toBe(false), { timeout: 2000 });
  fireEvent.click(screen.getByRole("button", { name: "Submit suggestion" }));
  await screen.findByRole("heading", { name: "Suggestion received" });
  expect(screen.getByText(/Pending review/)).toBeTruthy();
  expect(screen.getByText("Reader blog")).toBeTruthy();
  expect(document.activeElement?.textContent).toBe("Suggestion received");
  const post = fetcher.mock.calls.find(([url, init]) => init?.method === "POST" && !url.endsWith("preview"))!;
  expect(post[0]).toBe("/api/v1/user/sources/suggestions");
  expect(post[1]?.headers).toEqual({ "Content-Type": "application/json", "X-CSRF-Token": "csrf" });
  expect(JSON.parse(String(post[1]?.body))).toEqual({ feed_url: "https://example.com/rss", name: "Reader blog", source_type: "publisher" });
});
it.each([409, 422, 429, 503])("retains entered details after a %s response", async status => {
  setup(true, status);
  render(<UserProvider><SourceSuggestion /></UserProvider>);
  const input = await screen.findByLabelText("RSS or Atom URL");
  fireEvent.change(input, { target: { value: "https://example.com/rss" } });
  await waitFor(() => expect((screen.getByRole("button", { name: "Submit suggestion" }) as HTMLButtonElement).disabled).toBe(false), { timeout: 2000 });
  fireEvent.click(screen.getByRole("button", { name: "Submit suggestion" }));
  await screen.findByRole("alert");
  expect((input as HTMLInputElement).value).toBe("https://example.com/rss");
  await waitFor(() => expect((screen.getByRole("button", { name: "Submit suggestion" }) as HTMLButtonElement).disabled).toBe(false));
});

it("fills the feed name before submission and lets the reader edit it", async () => {
  const fetcher = setup();
  render(<UserProvider><SourceSuggestion /></UserProvider>);
  fireEvent.change(await screen.findByLabelText("RSS or Atom URL"), { target: { value: "https://example.com/rss" } });
  await waitFor(() => expect((screen.getByLabelText("Name (optional)") as HTMLInputElement).value).toBe("Feed title"), { timeout: 2000 });
  expect(fetcher.mock.calls.filter(([url]) => url.endsWith("sources/suggestions"))).toHaveLength(0);
  fireEvent.change(screen.getByLabelText("Name (optional)"), { target: { value: "My edited name" } });
  fireEvent.click(screen.getByRole("button", { name: "Submit suggestion" }));
  await screen.findByRole("heading", { name: "Suggestion received" });
  const post = fetcher.mock.calls.find(([url]) => url.endsWith("sources/suggestions"))!;
  expect(JSON.parse(String(post[1]?.body)).name).toBe("My edited name");
});
it("does not replace a manually edited name when the lookup finishes", async () => {
  setup();
  render(<UserProvider><SourceSuggestion /></UserProvider>);
  fireEvent.change(await screen.findByLabelText("RSS or Atom URL"), { target: { value: "https://example.com/rss" } });
  fireEvent.change(screen.getByLabelText("Name (optional)"), { target: { value: "Keep this name" } });
  await waitFor(() => expect(screen.queryByText("Looking up the feed name…")).toBeNull(), { timeout: 2000 });
  expect((screen.getByLabelText("Name (optional)") as HTMLInputElement).value).toBe("Keep this name");
});
