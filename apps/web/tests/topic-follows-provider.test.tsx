// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { TopicFollowsProvider, useTopicFollows } from "@/components/topic-follows";
import { TopicFollow } from "@/components/topic-follow";
import { userRequest, type UserIdentity } from "@/lib/user";

const account = vi.hoisted(() => ({ user: null as UserIdentity | null, loading: false }));
vi.mock("@/components/user-account", () => ({ useUser: () => account }));
vi.mock("@/lib/user", async (original) => ({
  ...(await original<typeof import("@/lib/user")>()),
  userRequest: vi.fn(),
}));
const user = (user_id: string): UserIdentity => ({
  user_id,
  csrf_token: user_id,
  name: null,
  email: null,
  expires_at: 4102444800,
});
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
function Controls() {
  const follows = useTopicFollows();
  return (
    <>
      <output data-testid="ids">{follows.ids.join(",")}</output>
      <button onClick={() => void follows.save(["bulk"])}>Bulk save</button>
    </>
  );
}
function App({ count = 2 }: { count?: number }) {
  return (
    <TopicFollowsProvider>
      <Controls />
      {Array.from({ length: count }, (_, i) => (
        <TopicFollow key={i} topicId={i < 2 ? "same" : `topic-${i}`} />
      ))}
    </TopicFollowsProvider>
  );
}
beforeEach(() => {
  account.user = user("first");
  vi.mocked(userRequest)
    .mockReset()
    .mockImplementation(async (path, init) =>
      init?.method === "PUT"
        ? path === "preferences"
          ? { topic_ids: ["bulk"] }
          : { followed: true }
        : { topic_ids: [] },
    );
});
afterEach(cleanup);

it("loads preferences once for 60 buttons and synchronizes duplicate topic buttons", async () => {
  const view = render(<App count={60} />);
  await waitFor(() =>
    expect(screen.getAllByRole("button", { name: "Follow" })[0]).toHaveProperty("disabled", false),
  );
  expect(userRequest).toHaveBeenCalledTimes(1);
  const pending = deferred<{ followed: boolean }>();
  vi.mocked(userRequest).mockImplementationOnce(() => pending.promise);
  const buttons = screen.getAllByRole("button", { name: "Follow" });
  fireEvent.click(buttons[0]);
  expect(buttons[1]).toHaveProperty("disabled", true);
  fireEvent.click(buttons[1]);
  expect(userRequest).toHaveBeenCalledTimes(2);
  await act(async () => pending.resolve({ followed: true }));
  expect(screen.getAllByRole("button", { name: "Following" })).toHaveLength(2);
  view.rerender(<App count={100} />);
  expect(userRequest).toHaveBeenCalledTimes(2);
  fireEvent.click(screen.getByRole("button", { name: "Bulk save" }));
  await waitFor(() => expect(screen.getByTestId("ids").textContent).toBe("bulk"));
  expect(screen.queryAllByRole("button", { name: "Following" })).toHaveLength(0);
  expect(vi.mocked(userRequest).mock.calls.filter(([, init]) => !init?.method)).toHaveLength(1);
});

it("merges concurrent writes for different topics", async () => {
  render(<App count={3} />);
  await waitFor(() =>
    expect(screen.getAllByRole("button", { name: "Follow" })[0]).toHaveProperty("disabled", false),
  );
  const first = deferred<{ followed: boolean }>();
  const second = deferred<{ followed: boolean }>();
  vi.mocked(userRequest)
    .mockImplementationOnce(() => first.promise)
    .mockImplementationOnce(() => second.promise);
  const buttons = screen.getAllByRole("button", { name: "Follow" });
  fireEvent.click(buttons[0]);
  fireEvent.click(buttons[2]);
  await act(async () => second.resolve({ followed: true }));
  await act(async () => first.resolve({ followed: true }));
  expect(screen.getAllByRole("button", { name: "Following" })).toHaveLength(3);
});

it("aborts an old account write and ignores its late result", async () => {
  const view = render(<App />);
  await waitFor(() =>
    expect(screen.getAllByRole("button", { name: "Follow" })[0]).toHaveProperty("disabled", false),
  );
  const pending = deferred<{ followed: boolean }>();
  vi.mocked(userRequest).mockImplementationOnce(() => pending.promise);
  fireEvent.click(screen.getAllByRole("button", { name: "Follow" })[0]);
  const signal = vi.mocked(userRequest).mock.calls[1][1]?.signal;
  account.user = user("second");
  view.rerender(<App />);
  expect(signal?.aborted).toBe(true);
  await waitFor(() => expect(userRequest).toHaveBeenCalledTimes(3));
  await act(async () => pending.resolve({ followed: true }));
  expect(screen.getByTestId("ids").textContent).toBe("");
  expect(screen.queryAllByRole("button", { name: "Following" })).toHaveLength(0);
});

it("does not request private preferences for guests", () => {
  account.user = null;
  render(<App count={60} />);
  expect(userRequest).not.toHaveBeenCalled();
  expect(screen.getAllByRole("link", { name: "Follow" })).toHaveLength(60);
});
