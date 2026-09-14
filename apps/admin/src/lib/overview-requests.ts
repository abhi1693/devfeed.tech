/** Keep independent chart requests from overwhelming the reporting connection budget. */
const waiting: Array<() => void> = [];
let active = 0;
const limit = 4;

export async function overviewRequest<T>(signal: AbortSignal, load: () => Promise<T>): Promise<T> {
  await new Promise<void>((resolve, reject) => {
    const start = () => {
      signal.removeEventListener("abort", cancel);
      active++;
      resolve();
    };
    const cancel = () => {
      const index = waiting.indexOf(start);
      if (index >= 0) waiting.splice(index, 1);
      reject(signal.reason);
    };
    if (signal.aborted) return reject(signal.reason);
    if (active < limit) start();
    else {
      waiting.push(start);
      signal.addEventListener("abort", cancel, { once: true });
    }
  });
  try {
    signal.throwIfAborted();
    return await load();
  } finally {
    active--;
    waiting.shift()?.();
  }
}
