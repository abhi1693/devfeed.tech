// @vitest-environment jsdom
import { StrictMode, useEffect } from "react";
import { QueryClient, useQueryClient } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ReaderQueryProvider } from "@/components/reader-query-provider";
import { useFollowPreferences } from "@/lib/use-follow-preferences";
import { AccountError, userRequest, type UserIdentity } from "@/lib/user";

const account = vi.hoisted(() => ({
  user: null as UserIdentity | null,
  loading: false,
  sessionRevision: 1,
}));
vi.mock("@/components/user-account", () => ({ useUser: () => account }));
vi.mock("@/lib/user", async (original) => ({
  ...(await original<typeof import("@/lib/user")>()),
  userRequest: vi.fn(),
}));
let client: QueryClient;
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
function Controls({ kind, copy }: { kind: "source" | "topic"; copy: number }) {
  const queryClient = useQueryClient();
  useEffect(() => {
    client = queryClient;
  }, [queryClient]);
  const follows = useFollowPreferences(kind);
  return (
    <section aria-label={`Controls ${copy}`}>
      <output data-testid={`ids-${copy}`}>{follows.ids.join(",")}</output>
      <output data-testid={`error-${copy}`}>{Object.values(follows.errors).join("")}</output>
      <output data-testid={`unavailable-${copy}`}>{String(follows.unavailable)}</output>
      <button disabled={follows.loading} onClick={() => void follows.toggle("a")}>
        Follow A {copy}
      </button>
      <button onClick={() => void follows.toggle("b")}>Follow B {copy}</button>
      <button onClick={() => void follows.save(["bulk"])}>Bulk {copy}</button>
      <button onClick={follows.refresh}>Refresh {copy}</button>
    </section>
  );
}
function App({ kind, copies = 1 }: { kind: "source" | "topic"; copies?: number }) {
  return (
    <ReaderQueryProvider>
      {Array.from({ length: copies }, (_, copy) => (
        <Controls key={copy} kind={kind} copy={copy} />
      ))}
    </ReaderQueryProvider>
  );
}
beforeEach(() => {
  account.user = {
    user_id: "reader",
    csrf_token: "csrf",
    name: null,
    email: null,
    expires_at: 4102444800,
  };
  account.sessionRevision = 1;
  vi.mocked(userRequest).mockReset();
});
afterEach(cleanup);

for (const kind of ["topic", "source"] as const) {
  const path = kind === "topic" ? "preferences" : "preferences/sources";
  const field = `${kind}_ids`;
  const ready = () =>
    waitFor(() =>
      expect(screen.getByRole("button", { name: "Follow A 0" })).toHaveProperty("disabled", false),
    );
  const puts = () => vi.mocked(userRequest).mock.calls.filter(([, init]) => init?.method === "PUT");

  it(`${kind}: deduplicates reads and duplicate writes across independent consumers`, async () => {
    vi.mocked(userRequest).mockResolvedValue({ [field]: [] });
    render(<App kind={kind} copies={2} />);
    await ready();
    expect(userRequest).toHaveBeenCalledTimes(1);
    const write = deferred<{ followed: boolean }>();
    vi.mocked(userRequest).mockImplementationOnce(() => write.promise);
    fireEvent.click(screen.getByRole("button", { name: "Follow A 0" }));
    fireEvent.click(screen.getByRole("button", { name: "Follow A 1" }));
    await waitFor(() => expect(puts()).toHaveLength(1));
    await act(async () => write.resolve({ followed: true }));
    await waitFor(() => expect(screen.getByTestId("ids-1").textContent).toBe("a"));
    expect(screen.getByTestId("ids-0").textContent).toBe("a");
    expect(userRequest).toHaveBeenCalledTimes(2);
  });

  it(`${kind}: merges concurrent item writes and blocks conflicting bulk saves`, async () => {
    vi.mocked(userRequest).mockResolvedValue({ [field]: ["outside-page"] });
    render(<App kind={kind} />);
    await ready();
    const a = deferred<{ followed: boolean }>();
    const b = deferred<{ followed: boolean }>();
    vi.mocked(userRequest)
      .mockImplementationOnce(() => a.promise)
      .mockImplementationOnce(() => b.promise);
    fireEvent.click(screen.getByRole("button", { name: "Follow A 0" }));
    fireEvent.click(screen.getByRole("button", { name: "Follow B 0" }));
    fireEvent.click(screen.getByRole("button", { name: "Bulk 0" }));
    await waitFor(() => expect(puts()).toHaveLength(2));
    await act(async () => b.resolve({ followed: true }));
    await act(async () => a.resolve({ followed: true }));
    await waitFor(() => expect(screen.getByTestId("ids-0").textContent).toBe("outside-page,b,a"));
    expect(puts()).toHaveLength(2);
  });

  it(`${kind}: aborts pending writes on credential refresh and ignores their late result`, async () => {
    vi.mocked(userRequest).mockResolvedValue({ [field]: [] });
    const view = render(<App kind={kind} />);
    await ready();
    const write = deferred<{ followed: boolean }>();
    vi.mocked(userRequest).mockImplementationOnce(() => write.promise);
    fireEvent.click(screen.getByRole("button", { name: "Follow A 0" }));
    await waitFor(() => expect(puts()).toHaveLength(1));
    const signal = puts()[0][1]!.signal!;
    account.user = { ...account.user!, csrf_token: "new-csrf" };
    account.sessionRevision++;
    vi.mocked(userRequest).mockResolvedValue({ [field]: ["new-session"] });
    view.rerender(<App kind={kind} />);
    expect(signal.aborted).toBe(true);
    await waitFor(() => expect(screen.getByTestId("ids-0").textContent).toBe("new-session"));
    await act(async () => write.resolve({ followed: true }));
    expect(screen.getByTestId("ids-0").textContent).toBe("new-session");
    vi.mocked(userRequest).mockResolvedValue({ followed: true });
    fireEvent.click(screen.getByRole("button", { name: "Follow A 0" }));
    await waitFor(() => expect(puts()).toHaveLength(2));
    expect(puts()[1][1]!.headers).toEqual({
      "Content-Type": "application/json",
      "X-CSRF-Token": "new-csrf",
    });
  });

  it(`${kind}: cancels a late read on sign-out and never fetches guest preferences`, async () => {
    const read = deferred<Record<string, string[]>>();
    vi.mocked(userRequest).mockImplementationOnce(() => read.promise);
    const view = render(<App kind={kind} />);
    await waitFor(() => expect(userRequest).toHaveBeenCalledTimes(1));
    const signal = vi.mocked(userRequest).mock.calls[0][1]!.signal!;
    account.user = null;
    view.rerender(<App kind={kind} />);
    expect(signal.aborted).toBe(true);
    await act(async () => read.resolve({ [field]: ["private-id"] }));
    expect(screen.getByTestId("ids-0").textContent).toBe("");
    fireEvent.click(screen.getByRole("button", { name: "Follow A 0" }));
    fireEvent.click(screen.getByRole("button", { name: "Refresh 0" }));
    expect(userRequest).toHaveBeenCalledTimes(1);
  });

  it(`${kind}: retries a failed read on resume without automatic retry loops`, async () => {
    vi.mocked(userRequest).mockRejectedValueOnce(new AccountError(503));
    render(<App kind={kind} />);
    await waitFor(() => expect(screen.getByTestId("unavailable-0").textContent).toBe("true"));
    expect(userRequest).toHaveBeenCalledTimes(1);
    vi.mocked(userRequest).mockResolvedValue({ [field]: ["recovered"] });
    fireEvent.focus(window);
    await waitFor(() => expect(screen.getByTestId("ids-0").textContent).toBe("recovered"));
    expect(userRequest).toHaveBeenCalledTimes(2);
  });

  it(`${kind}: preserves confirmed IDs and exposes a limit failure without replaying the write`, async () => {
    vi.mocked(userRequest).mockResolvedValue({ [field]: ["outside-page"] });
    render(<App kind={kind} />);
    await ready();
    vi.mocked(userRequest).mockRejectedValueOnce(new AccountError(422));
    fireEvent.click(screen.getByRole("button", { name: "Follow A 0" }));
    await waitFor(() => expect(screen.getByTestId("error-0").textContent).toContain("100"));
    expect(screen.getByTestId("ids-0").textContent).toBe("outside-page");
    expect(puts()).toHaveLength(1);
    expect(puts()[0][0]).toBe(kind === "topic" ? "preferences/topics/a" : `${path}/a`);
    expect(puts()[0][1]!.body).toBe('{"followed":true}');
  });

  it(`${kind}: coalesces repeated resume events after a failed background refresh`, async () => {
    vi.mocked(userRequest).mockResolvedValue({ [field]: ["confirmed"] });
    render(<App kind={kind} />);
    await ready();
    vi.mocked(userRequest).mockRejectedValueOnce(new AccountError(503));
    fireEvent.click(screen.getByRole("button", { name: "Refresh 0" }));
    await waitFor(() => expect(screen.getByTestId("unavailable-0").textContent).toBe("true"));
    const read = deferred<Record<string, string[]>>();
    vi.mocked(userRequest).mockImplementationOnce(() => read.promise);
    fireEvent.focus(window);
    fireEvent.focus(window);
    fireEvent(document, new Event("visibilitychange"));
    expect(userRequest).toHaveBeenCalledTimes(3);
    await act(async () => read.resolve({ [field]: ["recovered"] }));
    await waitFor(() => expect(screen.getByTestId("ids-0").textContent).toBe("recovered"));
  });

  it(`${kind}: cancels an older refresh before saving so it cannot overwrite the result`, async () => {
    vi.mocked(userRequest).mockResolvedValue({ [field]: [] });
    render(<App kind={kind} />);
    await ready();
    const read = deferred<Record<string, string[]>>();
    vi.mocked(userRequest)
      .mockImplementationOnce(() => read.promise)
      .mockResolvedValue({ followed: true });
    fireEvent.click(screen.getByRole("button", { name: "Refresh 0" }));
    await waitFor(() => expect(userRequest).toHaveBeenCalledTimes(2));
    const signal = vi.mocked(userRequest).mock.calls[1][1]!.signal!;
    fireEvent.click(screen.getByRole("button", { name: "Follow A 0" }));
    await waitFor(() => expect(screen.getByTestId("ids-0").textContent).toBe("a"));
    expect(signal.aborted).toBe(true);
    await act(async () => read.resolve({ [field]: ["stale"] }));
    expect(screen.getByTestId("ids-0").textContent).toBe("a");
  });

  it(`${kind}: works in Strict Mode and clears private queries and writes on unmount`, async () => {
    vi.mocked(userRequest).mockResolvedValue({ [field]: [] });
    const view = render(
      <StrictMode>
        <App kind={kind} />
      </StrictMode>,
    );
    await ready();
    const write = deferred<{ followed: boolean }>();
    vi.mocked(userRequest).mockImplementationOnce(() => write.promise);
    fireEvent.click(screen.getByRole("button", { name: "Follow A 0" }));
    await waitFor(() => expect(puts()).toHaveLength(1));
    const signal = puts()[0][1]!.signal!;
    const previous = client;
    view.unmount();
    expect(signal.aborted).toBe(true);
    await act(async () => write.resolve({ followed: true }));
    expect(previous.getQueryCache().getAll()).toHaveLength(0);
    expect(previous.getMutationCache().getAll()).toHaveLength(0);
  });
}
