/** Bound a read, including local queue time, without treating expiry as navigation cancellation. */
export const readRequestDeadlineMs = 15_000;

export class ReadRequestTimeout extends Error {
  constructor() {
    super("Loading took too long. Please retry.");
    this.name = "ReadRequestTimeout";
  }
}

export async function readRequest<T>(
  signal: AbortSignal,
  load: (signal: AbortSignal) => Promise<T>,
): Promise<T> {
  const controller = new AbortController();
  const requestSignal = AbortSignal.any([signal, controller.signal]);
  let timer: ReturnType<typeof setTimeout>;
  let cancel: () => void = () => {};
  const deadline = new Promise<never>((_resolve, reject) => {
    cancel = () => {
      reject(signal.reason);
    };
    signal.addEventListener("abort", cancel, { once: true });
    timer = setTimeout(() => {
      const error = new ReadRequestTimeout();
      controller.abort(error);
      reject(error);
    }, readRequestDeadlineMs);
  });
  try {
    signal.throwIfAborted();
    return await Promise.race([load(requestSignal), deadline]);
  } finally {
    clearTimeout(timer!);
    signal.removeEventListener("abort", cancel);
  }
}
