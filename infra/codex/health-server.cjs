// Serve probes inside the existing bridge process; no fresh Node runtime per check.
const http = require("node:http");
const net = require("node:net");

async function checkReady({ upstreamUrl, socketPath, timeout = 2500 }) {
  const deadline = Date.now() + timeout;
  try {
    const response = await fetch(upstreamUrl, { signal: AbortSignal.timeout(timeout) });
    await response.body?.cancel();
    if (!response.ok || Date.now() >= deadline) return false;
    return await new Promise((resolve) => {
      const socket = net.createConnection(socketPath);
      const finish = (ready) => {
        socket.destroy();
        resolve(ready);
      };
      socket.setTimeout(Math.max(1, deadline - Date.now()));
      socket.once("connect", () => finish(true));
      socket.once("timeout", () => finish(false));
      socket.once("error", () => finish(false));
    });
  } catch {
    return false;
  }
}

function createHealthServer({
  upstreamUrl = "http://127.0.0.1:4500/readyz",
  socketPath = "/run/codex/app-server.sock",
  isRunning = () => true,
  check = () => checkReady({ upstreamUrl, socketPath }),
} = {}) {
  let pending = null;
  let stopped = false;
  const server = http.createServer(async (request, response) => {
    if (request.url !== "/readyz") return response.writeHead(404).end();
    if (request.method !== "GET") return response.writeHead(405).end();
    if (stopped || !isRunning()) return response.writeHead(503).end("unavailable\n");
    // Readiness and liveness can arrive together. Share only the current check,
    // never a previous success: every later probe must observe current health.
    pending ??= Promise.resolve()
      .then(check)
      .catch(() => false)
      .finally(() => {
        pending = null;
      });
    const ready = await pending;
    response
      .writeHead(ready && !stopped && isRunning() ? 200 : 503, {
        "Cache-Control": "no-store",
        "Content-Type": "text/plain",
      })
      .end(ready && !stopped && isRunning() ? "ready\n" : "unavailable\n");
  });
  server.requestTimeout = 3000;
  server.headersTimeout = 3000;
  server.stop = () => {
    if (stopped) return;
    stopped = true;
    server.close();
    server.closeAllConnections();
  };
  return server;
}

module.exports = { checkReady, createHealthServer };
