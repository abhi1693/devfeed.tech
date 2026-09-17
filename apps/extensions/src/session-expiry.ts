// Browser timers overflow above this delay, including normal 30-day sessions.
const maxTimerDelay = 2 ** 31 - 1;

export function scheduleSessionExpiry(expiresAt: number, expire: () => void) {
  let timer: ReturnType<typeof setTimeout>;
  const check = () => {
    const remaining = expiresAt * 1000 - Date.now();
    if (remaining <= 0) {
      expire();
      return;
    }
    timer = setTimeout(check, Math.min(remaining, maxTimerDelay));
  };
  check();
  return () => clearTimeout(timer);
}
