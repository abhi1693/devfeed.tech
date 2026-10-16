import { afterEach, expect, it, vi } from "vitest";
import {
  readRequestBody,
  RequestBodyTimeout,
  RequestBodyTooLarge,
} from "@devfeed/http/request-body";

const encoder = new TextEncoder();
const request = (body?: BodyInit, headers?: HeadersInit, signal?: AbortSignal) =>
  new Request("https://devfeed.tech/api/event", {
    method: "POST",
    body,
    headers,
    signal,
    duplex: "half",
  } as RequestInit);

afterEach(() => vi.useRealTimers());

it("reads empty requests and preserves complete UTF-8 payloads at the byte limit", async () => {
  expect((await readRequestBody(request(), 2048)).byteLength).toBe(0);
  for (const value of ["x".repeat(2048), "é".repeat(1024)]) {
    const body = await readRequestBody(request(value), 2048);
    expect(body.byteLength).toBe(2048);
    expect(new TextDecoder().decode(body)).toBe(value);
  }
});

it("rejects declared oversize bodies before acquiring a reader and cancels their stream", async () => {
  const cancel = vi.fn();
  const stream = new ReadableStream<Uint8Array>({ cancel });
  const incoming = request(stream, { "Content-Length": "2049" });
  const read = vi.spyOn(incoming.body!, "getReader");
  await expect(readRequestBody(incoming, 2048)).rejects.toBeInstanceOf(RequestBodyTooLarge);
  expect(read).not.toHaveBeenCalled();
  expect(cancel).toHaveBeenCalledOnce();
});

it("counts accumulated chunk bytes, distrusts smaller declarations and cancels at the limit", async () => {
  const cancel = vi.fn();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(encoder.encode("é".repeat(512)));
      controller.enqueue(encoder.encode("é".repeat(513)));
      controller.enqueue(encoder.encode("never consumed"));
    },
    cancel,
  });
  const incoming = request(stream, { "Content-Length": "1" });
  await expect(readRequestBody(incoming, 2048)).rejects.toBeInstanceOf(RequestBodyTooLarge);
  expect(cancel).toHaveBeenCalledOnce();
  expect(incoming.body!.locked).toBe(false);
});

it("preserves byte sequences when multibyte characters cross chunk boundaries", async () => {
  const bytes = encoder.encode("é🙂");
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(bytes.slice(0, 1));
      controller.enqueue(bytes.slice(1, 4));
      controller.enqueue(bytes.slice(4));
      controller.close();
    },
  });
  const incoming = request(stream);
  expect(new Uint8Array(await readRequestBody(incoming, bytes.length))).toEqual(bytes);
  expect(incoming.body!.locked).toBe(false);
});

it("ignores empty chunks while preserving the exact byte boundary", async () => {
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (let index = 0; index < 100; index++) controller.enqueue(new Uint8Array());
      controller.enqueue(encoder.encode("é".repeat(1024)));
      controller.close();
    },
  });
  expect(new TextDecoder().decode(await readRequestBody(request(stream), 2048))).toBe(
    "é".repeat(1024),
  );
});

it.each([true, false])(
  "still rejects oversize when stream cancellation fails (declared=%s)",
  async (declared) => {
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new Uint8Array(2049));
      },
      cancel() {
        throw new Error("Peer disconnected");
      },
    });
    const incoming = request(stream, declared ? { "Content-Length": "2049" } : undefined);
    await expect(readRequestBody(incoming, 2048)).rejects.toBeInstanceOf(RequestBodyTooLarge);
    expect(incoming.body!.locked).toBe(false);
  },
);

it("propagates read failures while releasing the reader lock", async () => {
  const error = new Error("Disconnected body");
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.error(error);
    },
  });
  const incoming = request(stream);
  await expect(readRequestBody(incoming, 2048)).rejects.toBe(error);
  expect(incoming.body!.locked).toBe(false);
});

it.each(["stall", "fail"])(
  "cancels a stalled body at its deadline even when cancellation can %s",
  async (behavior) => {
    vi.useFakeTimers();
    const cancel = vi.fn(() =>
      behavior === "stall"
        ? new Promise<void>(() => {})
        : Promise.reject(new Error("Peer disconnected")),
    );
    const incoming = request(new ReadableStream<Uint8Array>({ cancel }));
    const pending = readRequestBody(incoming, 2048, 2000);
    const rejected = expect(pending).rejects.toBeInstanceOf(RequestBodyTimeout);
    await vi.advanceTimersByTimeAsync(1999);
    expect(cancel).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    await rejected;
    expect(cancel).toHaveBeenCalledOnce();
    expect(incoming.body!.locked).toBe(false);
    expect(vi.getTimerCount()).toBe(0);
  },
);

it("keeps one total deadline when chunks arrive without completing the body", async () => {
  vi.useFakeTimers();
  let controller: ReadableStreamDefaultController<Uint8Array>;
  const cancel = vi.fn();
  const incoming = request(
    new ReadableStream<Uint8Array>({
      start(value) {
        controller = value;
      },
      cancel,
    }),
  );
  const pending = readRequestBody(incoming, 2048, 2000);
  const rejected = expect(pending).rejects.toBeInstanceOf(RequestBodyTimeout);
  await vi.advanceTimersByTimeAsync(1000);
  controller!.enqueue(encoder.encode("first chunk"));
  await vi.advanceTimersByTimeAsync(999);
  controller!.enqueue(encoder.encode("second chunk"));
  expect(cancel).not.toHaveBeenCalled();
  await vi.advanceTimersByTimeAsync(1);
  await rejected;
  expect(cancel).toHaveBeenCalledOnce();
  expect(incoming.body!.locked).toBe(false);
});

it("clears the deadline on successful reads and read failures", async () => {
  vi.useFakeTimers();
  let controller: ReadableStreamDefaultController<Uint8Array>;
  const incoming = request(
    new ReadableStream<Uint8Array>({
      start(value) {
        controller = value;
      },
    }),
  );
  const pending = readRequestBody(incoming, 2048, 2000);
  await vi.advanceTimersByTimeAsync(500);
  controller!.enqueue(encoder.encode("complete "));
  await vi.advanceTimersByTimeAsync(500);
  controller!.enqueue(encoder.encode("body"));
  controller!.close();
  expect(new TextDecoder().decode(await pending)).toBe("complete body");
  expect(incoming.body!.locked).toBe(false);
  expect(vi.getTimerCount()).toBe(0);
  const error = new Error("Disconnected body");
  const failed = request(
    new ReadableStream<Uint8Array>({
      start(controller) {
        controller.error(error);
      },
    }),
  );
  await expect(readRequestBody(failed, 2048, 2000)).rejects.toBe(error);
  expect(failed.body!.locked).toBe(false);
  expect(vi.getTimerCount()).toBe(0);
});

it("allows reads without a deadline to continue until their body completes", async () => {
  vi.useFakeTimers();
  let controller: ReadableStreamDefaultController<Uint8Array>;
  const cancel = vi.fn();
  const incoming = request(
    new ReadableStream<Uint8Array>({
      start(value) {
        controller = value;
      },
      cancel,
    }),
  );
  const pending = readRequestBody(incoming, 2048);
  expect(vi.getTimerCount()).toBe(0);
  await vi.advanceTimersByTimeAsync(10000);
  expect(cancel).not.toHaveBeenCalled();
  controller!.enqueue(encoder.encode("later body"));
  controller!.close();
  expect(new TextDecoder().decode(await pending)).toBe("later body");
});

it("rejects an already aborted request before reading or canceling its body", async () => {
  const controller = new AbortController();
  const reason = new DOMException("Reader left", "AbortError");
  controller.abort(reason);
  const cancel = vi.fn();
  const incoming = request(
    new ReadableStream<Uint8Array>({ cancel }),
    undefined,
    controller.signal,
  );
  const read = vi.spyOn(incoming.body!, "getReader");
  await expect(readRequestBody(incoming, 2048, 2000)).rejects.toBe(reason);
  expect(read).not.toHaveBeenCalled();
  expect(cancel).not.toHaveBeenCalled();
  expect(incoming.bodyUsed).toBe(false);
});

it.each([undefined, 2000])(
  "interrupts a pending read through its request signal with deadline %s",
  async (deadline) => {
    vi.useFakeTimers();
    const controller = new AbortController();
    const cancel = vi.fn();
    const incoming = request(
      new ReadableStream<Uint8Array>({ cancel }),
      undefined,
      controller.signal,
    );
    const remove = vi.spyOn(incoming.signal, "removeEventListener");
    const pending = readRequestBody(incoming, 2048, deadline);
    const reason = new DOMException("Account changed", "AbortError");
    const rejected = expect(pending).rejects.toBe(reason);
    controller.abort(reason);
    await rejected;
    expect(cancel).toHaveBeenCalledOnce();
    expect(incoming.body!.locked).toBe(false);
    expect(remove).toHaveBeenCalledWith("abort", expect.any(Function));
    expect(vi.getTimerCount()).toBe(0);
  },
);

it.each(["stall", "fail"])(
  "returns caller cancellation even when the stream's cancellation can %s",
  async (behavior) => {
    vi.useFakeTimers();
    const controller = new AbortController();
    const cancel = vi.fn(() =>
      behavior === "stall"
        ? new Promise<void>(() => {})
        : Promise.reject(new Error("Peer already disconnected")),
    );
    const incoming = request(
      new ReadableStream<Uint8Array>({ cancel }),
      undefined,
      controller.signal,
    );
    const pending = readRequestBody(incoming, 2048, 2000);
    const reason = new DOMException("Reader left", "AbortError");
    const rejected = expect(pending).rejects.toBe(reason);
    controller.abort(reason);
    await rejected;
    expect(cancel).toHaveBeenCalledOnce();
    expect(incoming.body!.locked).toBe(false);
    expect(vi.getTimerCount()).toBe(0);
  },
);

it("uses an explicit fourth signal instead of the original request signal", async () => {
  vi.useFakeTimers();
  const original = new AbortController();
  const override = new AbortController();
  const cancel = vi.fn();
  const incoming = request(new ReadableStream<Uint8Array>({ cancel }), undefined, original.signal);
  const remove = vi.spyOn(override.signal, "removeEventListener");
  const pending = readRequestBody(incoming, 2048, 2000, override.signal);
  original.abort();
  await vi.advanceTimersByTimeAsync(1);
  expect(cancel).not.toHaveBeenCalled();
  const reason = new DOMException("Overall deadline", "TimeoutError");
  const rejected = expect(pending).rejects.toBe(reason);
  override.abort(reason);
  await rejected;
  expect(cancel).toHaveBeenCalledOnce();
  expect(remove).toHaveBeenCalledWith("abort", expect.any(Function));
  expect(vi.getTimerCount()).toBe(0);
});

it("rejects an already aborted override while leaving the original body untouched", async () => {
  const controller = new AbortController();
  controller.abort();
  const incoming = request("{}", undefined);
  await expect(readRequestBody(incoming, 2048, 2000, controller.signal)).rejects.toBe(
    controller.signal.reason,
  );
  expect(incoming.signal.aborted).toBe(false);
  expect(incoming.bodyUsed).toBe(false);
});

it.each(["complete", "read failure", "oversize", "deadline"])(
  "removes the cancellation listener after a %s outcome",
  async (outcome) => {
    vi.useFakeTimers();
    const controller = new AbortController();
    const cancel = vi.fn();
    const failure = new Error("Disconnected body");
    const stream = new ReadableStream<Uint8Array>({
      start(value) {
        if (outcome === "read failure") value.error(failure);
        else if (outcome !== "deadline") {
          value.enqueue(new Uint8Array(outcome === "oversize" ? 2049 : 2048));
          if (outcome === "complete") value.close();
        }
      },
      cancel,
    });
    const incoming = request(stream, undefined, controller.signal);
    const remove = vi.spyOn(incoming.signal, "removeEventListener");
    const pending = readRequestBody(incoming, 2048, 2000);
    if (outcome === "complete") expect((await pending).byteLength).toBe(2048);
    else if (outcome === "read failure") await expect(pending).rejects.toBe(failure);
    else if (outcome === "oversize")
      await expect(pending).rejects.toBeInstanceOf(RequestBodyTooLarge);
    else {
      const rejected = expect(pending).rejects.toBeInstanceOf(RequestBodyTimeout);
      await vi.advanceTimersByTimeAsync(2000);
      await rejected;
    }
    expect(remove).toHaveBeenCalledWith("abort", expect.any(Function));
    expect(vi.getTimerCount()).toBe(0);
    const cancellations = cancel.mock.calls.length;
    controller.abort();
    expect(cancel).toHaveBeenCalledTimes(cancellations);
    expect(incoming.body!.locked).toBe(false);
  },
);
