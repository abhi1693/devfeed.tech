import { expect, it, vi } from "vitest";
import { columnFeedLimits, createColumnRequestQueue } from "@/lib/column-feed";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, resolve, reject };
}

it("starts at most three feed requests and starts waiting columns in order", async () => {
  const queue = createColumnRequestQueue();
  const work = Array.from({ length: 12 }, () => deferred<number>());
  const started: number[] = [];
  const responses = work.map((task, index) =>
    queue.run(() => {
      started.push(index);
      return task.promise;
    }, new AbortController().signal),
  );
  await vi.waitFor(() => expect(started).toEqual([0, 1, 2]));
  work[0].resolve(0);
  await vi.waitFor(() => expect(started).toEqual([0, 1, 2, 3]));
  for (let index = 1; index < work.length; index++) {
    work[index].resolve(index);
    await vi.waitFor(() => expect(started).toContain(Math.min(index + 3, work.length - 1)));
  }
  expect(await Promise.all(responses)).toEqual(Array.from({ length: 12 }, (_, index) => index));
  expect(columnFeedLimits.concurrentRequests).toBe(3);
});

it("removes an aborted waiting request without starting it or blocking the next column", async () => {
  const queue = createColumnRequestQueue();
  const running = Array.from({ length: columnFeedLimits.concurrentRequests }, () =>
    deferred<number>(),
  );
  const active = running.map((task) => queue.run(() => task.promise, new AbortController().signal));
  const cancelled = new AbortController();
  const task = vi.fn(async () => 99);
  const queued = queue.run(task, cancelled.signal);
  const rejection = expect(queued).rejects.toMatchObject({ name: "AbortError" });
  const next = vi.fn(async () => 100);
  const last = queue.run(next, new AbortController().signal);
  cancelled.abort();
  await rejection;
  expect(task).not.toHaveBeenCalled();
  running[0].resolve(0);
  await vi.waitFor(() => expect(next).toHaveBeenCalledOnce());
  running.slice(1).forEach((entry, index) => entry.resolve(index + 1));
  await Promise.all(active);
  expect(await last).toBe(100);
});

it("keeps an aborted active request's slot until its actual work settles", async () => {
  const queue = createColumnRequestQueue();
  const pending = deferred<number>();
  const controller = new AbortController();
  let requestSignal: AbortSignal | undefined;
  const first = queue.run((signal) => {
    requestSignal = signal;
    return pending.promise;
  }, controller.signal);
  const ignored = first.catch(() => undefined);
  const others = Array.from({ length: columnFeedLimits.concurrentRequests - 1 }, () =>
    deferred<number>(),
  );
  const running = others.map((task) => queue.run(() => task.promise, new AbortController().signal));
  const next = vi.fn(async () => 4);
  const queued = queue.run(next, new AbortController().signal);
  await vi.waitFor(() => expect(requestSignal).toBeDefined());
  controller.abort();
  expect(requestSignal!.aborted).toBe(true);
  await Promise.resolve();
  expect(next).not.toHaveBeenCalled();
  pending.resolve(1);
  await vi.waitFor(() => expect(next).toHaveBeenCalledOnce());
  others.forEach((task) => task.resolve(2));
  await Promise.all([ignored, queued, ...running]);
});

it("releases a failed request's slot and never starts a pre-aborted request", async () => {
  const queue = createColumnRequestQueue();
  const aborted = new AbortController();
  aborted.abort();
  const skipped = vi.fn(async () => 0);
  await expect(queue.run(skipped, aborted.signal)).rejects.toMatchObject({ name: "AbortError" });
  expect(skipped).not.toHaveBeenCalled();
  const first = deferred<number>();
  const failed = queue.run(() => first.promise, new AbortController().signal);
  const rejection = expect(failed).rejects.toThrow("Feed offline");
  const others = Array.from({ length: columnFeedLimits.concurrentRequests - 1 }, () =>
    deferred<number>(),
  );
  const running = others.map((task) => queue.run(() => task.promise, new AbortController().signal));
  const next = vi.fn(async () => 4);
  const queued = queue.run(next, new AbortController().signal);
  first.reject(new Error("Feed offline"));
  await rejection;
  await vi.waitFor(() => expect(next).toHaveBeenCalledOnce());
  others.forEach((task) => task.resolve(2));
  await Promise.all([queued, ...running]);
});
