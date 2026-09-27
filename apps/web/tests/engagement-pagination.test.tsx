// @vitest-environment jsdom
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  EngagementProvider,
  bookmarkChanged,
  useArticleEngagement,
} from "@/components/article-engagement";
import { userRequest, type UserIdentity } from "@/lib/user";

const account = vi.hoisted(() => ({ user: null as UserIdentity | null, loading: false }));
vi.mock("@/components/user-account", () => ({ useUser: () => account }));
vi.mock("@/lib/user", () => ({ userRequest: vi.fn() }));
const identity = (user_id: string): UserIdentity => ({
  user_id,
  name: null,
  email: null,
  csrf_token: "csrf",
  expires_at: 4102444800,
});
const engagement = (article_id: string) => ({
  article_id,
  likes: 2,
  opens: 5,
  liked: false,
  bookmarked: false,
});
const ids = (length: number) => Array.from({ length }, (_, index) => `article-${index}`);
const requested = (path: string) => new URLSearchParams(path.split("?")[1]).getAll("article_id");
function Value({ id }: { id: string }) {
  const value = useArticleEngagement(id);
  return <output data-testid={id}>{value ? JSON.stringify(value) : "missing"}</output>;
}
function Feed({ items }: { items: string[] }) {
  return (
    <EngagementProvider articleIds={items}>
      {[...new Set(items)].map((id) => (
        <Value key={id} id={id} />
      ))}
    </EngagementProvider>
  );
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
beforeEach(() => {
  account.user = identity("first");
  account.loading = false;
  vi.mocked(userRequest)
    .mockReset()
    .mockImplementation(async (path) => requested(path).map(engagement));
});
afterEach(cleanup);

it("requests only the next 24 missing IDs as five feed pages accumulate", async () => {
  const view = render(<Feed items={ids(24)} />);
  for (let page = 1; page <= 5; page++) {
    if (page > 1) view.rerender(<Feed items={ids(page * 24)} />);
    await waitFor(() =>
      expect(screen.getByTestId(`article-${page * 24 - 1}`).textContent).not.toContain("missing"),
    );
  }
  const calls = vi.mocked(userRequest).mock.calls.map(([path]) => requested(path));
  expect(calls.map((batch) => batch.length)).toEqual([24, 24, 24, 24, 24]);
  expect(calls.flat()).toEqual(ids(120));
  expect(screen.getByTestId("article-0").textContent).toContain('"likes":2');
  view.rerender(<Feed items={[...ids(120).reverse(), "article-0"]} />);
  expect(userRequest).toHaveBeenCalledTimes(5);
});

it("splits a large initial list into sequential batches of at most 100", async () => {
  const pending = deferred<ReturnType<typeof engagement>[]>();
  vi.mocked(userRequest).mockImplementationOnce(() => pending.promise);
  render(<Feed items={ids(225)} />);
  expect(userRequest).toHaveBeenCalledTimes(1);
  await act(async () => pending.resolve(ids(100).map(engagement)));
  await waitFor(() =>
    expect(screen.getByTestId("article-224").textContent).not.toContain("missing"),
  );
  expect(vi.mocked(userRequest).mock.calls.map(([path]) => requested(path).length)).toEqual([
    100, 100, 25,
  ]);
});

it("retains successful batches and retries only missing records after a failure", async () => {
  vi.mocked(userRequest).mockImplementationOnce(async (path) => requested(path).map(engagement));
  vi.mocked(userRequest).mockRejectedValueOnce(new Error("offline"));
  const view = render(<Feed items={ids(225)} />);
  await waitFor(() =>
    expect(screen.getByTestId("article-224").textContent).not.toContain("missing"),
  );
  expect(screen.getByTestId("article-150").textContent).toContain("missing");
  view.rerender(<Feed items={ids(226)} />);
  await waitFor(() =>
    expect(screen.getByTestId("article-150").textContent).not.toContain("missing"),
  );
  await waitFor(() =>
    expect(screen.getByTestId("article-225").textContent).not.toContain("missing"),
  );
  expect(
    vi
      .mocked(userRequest)
      .mock.calls.slice(3)
      .flatMap(([path]) => requested(path)),
  ).toEqual([...ids(200).slice(100), "article-225"]);
});

it("clears cached private state on account changes and rejects late responses", async () => {
  const pending = deferred<ReturnType<typeof engagement>[]>();
  vi.mocked(userRequest).mockImplementationOnce(() => pending.promise);
  const view = render(<Feed items={ids(1)} />);
  const signal = vi.mocked(userRequest).mock.calls[0][1]?.signal;
  account.user = identity("second");
  view.rerender(<Feed items={ids(1)} />);
  expect(signal?.aborted).toBe(true);
  await waitFor(() => expect(screen.getByTestId("article-0").textContent).not.toContain("missing"));
  await act(async () =>
    pending.resolve([{ ...engagement("article-0"), liked: true, bookmarked: true }]),
  );
  expect(screen.getByTestId("article-0").textContent).toContain('"liked":false');
  account.user = null;
  view.rerender(<Feed items={ids(1)} />);
  await waitFor(() => expect(userRequest).toHaveBeenCalledTimes(3));
});

it("preserves a like and bookmark received while a stale read is pending", async () => {
  const pending = deferred<ReturnType<typeof engagement>[]>();
  vi.mocked(userRequest).mockImplementationOnce(() => pending.promise);
  render(<Feed items={ids(2)} />);
  act(() => {
    window.dispatchEvent(
      new CustomEvent("devfeed:article-engagement", {
        detail: {
          owner: "first",
          value: { ...engagement("article-0"), likes: 3, liked: true },
        },
      }),
    );
    for (const article_id of ids(2))
      window.dispatchEvent(
        new CustomEvent(bookmarkChanged, {
          detail: {
            owner: "first",
            article_id,
            bookmarked: true,
          },
        }),
      );
  });
  await act(async () => pending.resolve(ids(2).map(engagement)));
  expect(screen.getByTestId("article-0").textContent).toContain('"likes":3');
  expect(screen.getByTestId("article-0").textContent).toContain('"liked":true');
  for (const id of ids(2))
    expect(screen.getByTestId(id).textContent).toContain('"bookmarked":true');
});

it("queues new pages without cancelling or duplicating in-flight IDs", async () => {
  const pending = deferred<ReturnType<typeof engagement>[]>();
  vi.mocked(userRequest).mockImplementationOnce(() => pending.promise);
  const view = render(<Feed items={ids(24)} />);
  const signal = vi.mocked(userRequest).mock.calls[0][1]?.signal;
  view.rerender(<Feed items={ids(48)} />);
  expect(signal?.aborted).toBe(false);
  expect(userRequest).toHaveBeenCalledTimes(1);
  await act(async () => pending.resolve(ids(24).map(engagement)));
  await waitFor(() =>
    expect(screen.getByTestId("article-47").textContent).not.toContain("missing"),
  );
  expect(vi.mocked(userRequest).mock.calls.map(([path]) => requested(path))).toEqual([
    ids(24),
    ids(48).slice(24),
  ]);
});
