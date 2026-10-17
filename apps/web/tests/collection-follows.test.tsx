// @vitest-environment jsdom
import { useState } from "react";
import { cleanup, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { TagFollowsProvider, useTagFollows } from "@/components/tag-follows";
import { TopicFollowsProvider, useTopicFollows } from "@/components/topic-follows";
import { ReaderQueryProvider } from "@/components/reader-query-provider";
import { AccountError, userRequest } from "@/lib/user";

vi.mock("@/components/user-account", () => ({
  useUser: () => ({
    user: { user_id: "reader", csrf_token: "csrf" },
    loading: false,
    sessionRevision: 1,
  }),
}));
vi.mock("@/lib/user", async (original) => ({
  ...(await original<typeof import("@/lib/user")>()),
  userRequest: vi.fn(),
}));
function Controls({ kind }: { kind: "tag" | "topic" }) {
  const tag = useTagFollows();
  const topic = useTopicFollows();
  const follows = kind === "tag" ? tag : topic;
  const [receipt, setReceipt] = useState("");
  return (
    <>
      <output data-testid={`${kind}-ids`}>{follows.ids.join(",")}</output>
      <output data-testid={`${kind}-receipt`}>{receipt}</output>
      <button
        disabled={follows.loading}
        onClick={async () => setReceipt(JSON.stringify(await follows.save([])))}
      >
        Clear {kind}
      </button>
      <button onClick={follows.refresh}>Refresh {kind}</button>
      <button onClick={() => void follows.toggle("one")}>Toggle {kind}</button>
    </>
  );
}
function App() {
  return (
    <ReaderQueryProvider>
      <TagFollowsProvider>
        <TopicFollowsProvider>
          <Controls kind="tag" />
          <Controls kind="topic" />
        </TopicFollowsProvider>
      </TagFollowsProvider>
    </ReaderQueryProvider>
  );
}
beforeEach(() => {
  vi.mocked(userRequest)
    .mockReset()
    .mockImplementation(async (path) =>
      path === "preferences/tags" ? { tag_ids: ["tag-a"] } : { topic_ids: ["topic-a"] },
    );
});
afterEach(cleanup);

it("adapts empty tag and topic saves to their public receipts without crossing collections", async () => {
  render(<App />);
  await waitFor(() => expect(screen.getByTestId("tag-ids").textContent).toBe("tag-a"));
  await waitFor(() => expect(screen.getByTestId("topic-ids").textContent).toBe("topic-a"));
  vi.mocked(userRequest).mockResolvedValueOnce({ tag_ids: [] });
  fireEvent.click(screen.getByRole("button", { name: "Clear tag" }));
  await waitFor(() => expect(screen.getByTestId("tag-receipt").textContent).toBe('{"tag_ids":[]}'));
  expect(screen.getByTestId("tag-ids").textContent).toBe("");
  expect(screen.getByTestId("topic-ids").textContent).toBe("topic-a");
  vi.mocked(userRequest).mockResolvedValueOnce({ topic_ids: [] });
  fireEvent.click(screen.getByRole("button", { name: "Clear topic" }));
  await waitFor(() =>
    expect(screen.getByTestId("topic-receipt").textContent).toBe('{"topic_ids":[]}'),
  );
});

it("returns no save receipt after failure and keeps the acknowledged selection", async () => {
  render(<App />);
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Clear tag" })).toHaveProperty("disabled", false),
  );
  vi.mocked(userRequest).mockRejectedValueOnce(new AccountError(503));
  fireEvent.click(screen.getByRole("button", { name: "Clear tag" }));
  await waitFor(() => expect(screen.getByTestId("tag-receipt").textContent).toBe("null"));
  expect(screen.getByTestId("tag-ids").textContent).toBe("tag-a");
});

it("keeps controls inert while their provider is unavailable", async () => {
  render(<Controls kind="tag" />);
  expect(screen.getByRole("button", { name: "Clear tag" })).toHaveProperty("disabled", true);
  fireEvent.click(screen.getByRole("button", { name: "Refresh tag" }));
  fireEvent.click(screen.getByRole("button", { name: "Toggle tag" }));
  const { result } = renderHook(useTagFollows);
  expect(await result.current.save(["tag-a"])).toBeNull();
  expect(userRequest).not.toHaveBeenCalled();
});
