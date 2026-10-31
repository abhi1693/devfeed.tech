const { test } = require("node:test");
const assert = require("node:assert/strict");
const http = require("node:http");
const net = require("node:net");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const { once } = require("node:events");
const { checkReady, createHealthServer } = require("../infra/codex/health-server.cjs");

async function listening(server) {
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  return `http://127.0.0.1:${server.address().port}`;
}

test("existing-process health checks both fresh readyz and the private bridge", async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "devfeed-health-"));
  const socketPath = path.join(directory, "app.sock");
  let status = 200;
  const upstream = http.createServer((_request, response) => response.writeHead(status).end());
  const socket = net.createServer((peer) => peer.end());
  let server;
  try {
    const upstreamUrl = `${await listening(upstream)}/readyz`;
    socket.listen(socketPath);
    await once(socket, "listening");
    server = createHealthServer({ upstreamUrl, socketPath });
    const origin = await listening(server);
    const probe = () => fetch(`${origin}/readyz`).then((response) => response.status);
    assert.equal(await probe(), 200);
    status = 503;
    assert.equal(await probe(), 503);
    status = 200;
    assert.equal(await probe(), 200);
    await new Promise((resolve) => socket.close(resolve));
    assert.equal(await probe(), 503);
    assert.equal((await fetch(`${origin}/other`)).status, 404);
    assert.equal((await fetch(`${origin}/readyz`, { method: "POST" })).status, 405);
  } finally {
    server?.stop();
    upstream.closeAllConnections();
    upstream.close();
    socket.close();
    await fs.rm(directory, { recursive: true, force: true });
  }
});

test("concurrent probes share only in-flight work, never a cached success", async () => {
  let release;
  let checks = 0;
  let running = true;
  const server = createHealthServer({
    isRunning: () => running,
    check: () => {
      checks++;
      return new Promise((resolve) => {
        release = resolve;
      });
    },
  });
  try {
    const origin = await listening(server);
    const first = fetch(`${origin}/readyz`);
    const second = fetch(`${origin}/readyz`);
    while (!release) await new Promise((resolve) => setTimeout(resolve, 1));
    await new Promise((resolve) => setTimeout(resolve, 10));
    assert.equal(checks, 1);
    release(true);
    const responses = await Promise.all([first, second]);
    assert.ok(responses.every((response) => response.status === 200));
    assert.equal(responses[0].headers.get("cache-control"), "no-store");
    const next = fetch(`${origin}/readyz`);
    while (checks !== 2) await new Promise((resolve) => setTimeout(resolve, 1));
    release(false);
    assert.equal((await next).status, 503);
    running = false;
    assert.equal((await fetch(`${origin}/readyz`)).status, 503);
    assert.equal(checks, 2);
  } finally {
    server.stop();
  }
});

test("unreachable, timed-out and rejected readiness checks remain unhealthy", async () => {
  const upstream = http.createServer(() => {});
  try {
    const upstreamUrl = `${await listening(upstream)}/readyz`;
    const started = performance.now();
    assert.equal(await checkReady({ upstreamUrl, socketPath: "/missing", timeout: 30 }), false);
    assert.ok(performance.now() - started < 1000);
    assert.equal(
      await checkReady({ upstreamUrl: "http://127.0.0.1:1", socketPath: "/missing", timeout: 30 }),
      false,
    );
    const server = createHealthServer({
      check: () => {
        throw new Error("private failure");
      },
    });
    try {
      const response = await fetch(`${await listening(server)}/readyz`);
      assert.equal(response.status, 503);
      assert.equal(await response.text(), "unavailable\n");
    } finally {
      server.stop();
    }
  } finally {
    upstream.closeAllConnections();
    upstream.close();
  }
});
