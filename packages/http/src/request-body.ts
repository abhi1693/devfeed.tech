export class RequestBodyTooLarge extends Error {
  constructor() {
    super("Request body exceeds limit");
    this.name = "RequestBodyTooLarge";
  }
}

export class RequestBodyTimeout extends Error {
  constructor() {
    super("Request body deadline exceeded");
    this.name = "RequestBodyTimeout";
  }
}

/** HTTP adapters must also close rejected connections before cancelling their input. */
export type AfterResponse = (callback: () => void) => void;

async function collectBody(
  reader: ReadableStreamDefaultReader<Uint8Array>,
  limit: number,
  cancel: () => void,
) {
  const chunks: Uint8Array[] = [];
  let size = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    if (value.byteLength === 0) continue;
    size += value.byteLength;
    if (size > limit) {
      // Peer disconnection must not turn an oversized request into an accepted body.
      cancel();
      throw new RequestBodyTooLarge();
    }
    chunks.push(value);
  }
  const buffer = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    buffer.set(chunk, offset);
    offset += chunk.length;
  }
  return buffer.buffer;
}

/** Bound bytes before decoding or parsing, with cancellation and a total read deadline. */
export async function readRequestBody(
  request: Request,
  limit: number,
  timeoutMs?: number,
  signal: AbortSignal = request.signal,
  afterResponse?: AfterResponse,
): Promise<ArrayBuffer> {
  signal.throwIfAborted();
  let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
  let cancelled = false;
  const cancel = (defer = true) => {
    if (cancelled) return;
    cancelled = true;
    const close = () => {
      // A deferred callback runs after finally has released the reader lock.
      void (reader ? reader.cancel() : request.body?.cancel())?.catch(() => undefined);
    };
    // Cancelling Node's incoming stream can destroy its HTTP socket. Next's
    // response-completion hook lets the 413/408 finish before closing input.
    if (defer && afterResponse) afterResponse(close);
    else close();
  };
  if (Number(request.headers.get("content-length")) > limit) {
    cancel();
    throw new RequestBodyTooLarge();
  }
  reader = request.body?.getReader();
  if (!reader) return new ArrayBuffer(0);
  let timer: ReturnType<typeof setTimeout> | undefined;
  let abort: (() => void) | undefined;
  try {
    const body = collectBody(reader, limit, cancel);
    const cancellation = new Promise<never>((_resolve, reject) => {
      abort = () => {
        reject(signal.reason);
        cancel(false);
      };
      signal.addEventListener("abort", abort, { once: true });
      if (signal.aborted) abort();
    });
    const pending = [body, cancellation];
    if (timeoutMs !== undefined) {
      pending.push(
        new Promise<never>((_resolve, reject) => {
          timer = setTimeout(() => {
            reject(new RequestBodyTimeout());
            // An unresponsive peer must not delay the deadline response.
            cancel();
          }, timeoutMs);
        }),
      );
    }
    return await Promise.race(pending);
  } finally {
    if (timer !== undefined) clearTimeout(timer);
    if (abort) signal.removeEventListener("abort", abort);
    reader.releaseLock();
    reader = undefined;
  }
}
