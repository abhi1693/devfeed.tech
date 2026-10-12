import "server-only";

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

async function collectBody(reader: ReadableStreamDefaultReader<Uint8Array>, limit: number) {
  const chunks: Uint8Array[] = [];
  let size = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    if (value.byteLength === 0) continue;
    size += value.byteLength;
    if (size > limit) {
      // Peer disconnection must not turn an oversized request into an accepted body.
      void reader.cancel().catch(() => undefined);
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

/** Bound bytes before decoding or parsing, optionally with a total body-read deadline. */
export async function readRequestBody(
  request: Request,
  limit: number,
  timeoutMs?: number,
): Promise<ArrayBuffer> {
  if (Number(request.headers.get("content-length")) > limit) {
    void request.body?.cancel().catch(() => undefined);
    throw new RequestBodyTooLarge();
  }
  const reader = request.body?.getReader();
  if (!reader) return new ArrayBuffer(0);
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    const body = collectBody(reader, limit);
    if (timeoutMs === undefined) return await body;
    const deadline = new Promise<never>((_resolve, reject) => {
      timer = setTimeout(() => {
        reject(new RequestBodyTimeout());
        // An unresponsive peer's cancellation must not delay the deadline response.
        void reader.cancel().catch(() => undefined);
      }, timeoutMs);
    });
    return await Promise.race([body, deadline]);
  } finally {
    if (timer !== undefined) clearTimeout(timer);
    reader.releaseLock();
  }
}
