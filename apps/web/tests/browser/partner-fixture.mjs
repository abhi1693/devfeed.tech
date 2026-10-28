export async function waitForPlacementServer(
  origin,
  { attempts = 100, delayMs = 200, requestTimeoutMs = 1000 } = {},
) {
  for (let attempt = 0; attempt < attempts; attempt++) {
    try {
      if ((await fetch(`${origin}/login`, { signal: AbortSignal.timeout(requestTimeoutMs) })).ok)
        return true;
    } catch {
      // The edge may accept requests before the application starts listening.
    }
    if (attempt + 1 < attempts) await new Promise((resolve) => setTimeout(resolve, delayMs));
  }
  return false;
}

export async function runPlacementStage(name, observe, { timeoutMs = 30_000 } = {}) {
  console.log(`[${new Date().toISOString()}] Placement ${name} BEGIN`);
  let deadline;
  try {
    const result = await Promise.race([
      Promise.resolve().then(observe),
      new Promise((_, reject) => {
        deadline = setTimeout(
          () => reject(new Error(`Placement stage ${name} timed out after ${timeoutMs}ms`)),
          timeoutMs,
        );
      }),
    ]);
    console.log(`[${new Date().toISOString()}] Placement ${name} END success`);
    return result;
  } finally {
    clearTimeout(deadline);
  }
}

export async function withPlacementDeliveryHeld(delivery, observe) {
  const gate = Promise.withResolvers();
  delivery.pending = gate.promise;
  try {
    return await observe();
  } finally {
    // Failed reading observations must also release pending fixture requests.
    delivery.pending = null;
    gate.resolve();
  }
}

export async function closePlacementFixture(server) {
  const closed = new Promise((resolve, reject) => {
    server.close((error) => (error ? reject(error) : resolve()));
  });
  // Stop accepting requests before aborting connections left by an interrupted scenario.
  server.closeAllConnections();
  await closed;
}
