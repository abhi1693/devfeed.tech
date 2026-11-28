const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const http = require("node:http");
const https = require("node:https");
const { execFileSync } = require("node:child_process");
const { once, EventEmitter } = require("node:events");
const { randomBytes } = require("node:crypto");
const vm = require("node:vm");
const { createTransport } = require("../infra/codex/transport.cjs");

test("server pins the Codex script and its Node interpreter independently of PATH", () => {
  const arguments_ = ["app-server", "--listen", "ws://127.0.0.1:4500"];
  const calls = [];
  const bridge = new EventEmitter();
  bridge.listen = () => {};
  const child = new EventEmitter();
  const modules = {
    "node:fs": { rmSync() {} },
    "node:net": { createServer: () => bridge },
    "node:child_process": {
      spawn: (...args) => {
        calls.push(args);
        return child;
      },
    },
    "./transport.cjs": {},
    "./health-server.cjs": { createHealthServer: () => ({ listen() {}, on() {}, stop() {} }) },
  };
  const filename = require.resolve("../infra/codex/server.cjs");
  vm.runInNewContext(
    fs.readFileSync(filename, "utf8"),
    {
      require: (name) => modules[name],
      process: {
        execPath: "/usr/local/bin/node",
        argv: ["node", "server.cjs", ...arguments_],
        env: { PATH: "/workspace/untrusted-bin" },
        umask() {},
        on() {},
      },
      console,
    },
    { filename },
  );
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], "/usr/local/bin/node");
  assert.deepEqual(Array.from(calls[0][1]), [
    "/usr/local/lib/node_modules/@openai/codex/bin/codex.js",
    ...arguments_,
  ]);
  assert.equal(calls[0][2].stdio, "inherit");
  assert.ok(!calls[0][2].shell);
});

test("TLS transport authenticates before connecting and strips its token", async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "devfeed-tls-test-"));
  let upgrades = 0;
  const upstream = http.createServer();
  const token = "test-service-token-with-at-least-32-characters";
  let server;
  try {
    execFileSync(
      "openssl",
      [
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-days",
        "1",
        "-subj",
        "/CN=localhost",
        "-addext",
        "subjectAltName=DNS:localhost",
        "-keyout",
        path.join(directory, "key.pem"),
        "-out",
        path.join(directory, "cert.pem"),
      ],
      { stdio: "ignore" },
    );
    upstream.on("upgrade", (request, socket) => {
      upgrades++;
      assert.equal(request.headers.authorization, undefined);
      socket.end(
        "HTTP/1.1 101 Switching Protocols\r\nConnection: Upgrade\r\nUpgrade: websocket\r\n\r\n",
      );
    });
    upstream.listen(0, "127.0.0.1");
    await once(upstream, "listening");
    const cert = fs.readFileSync(path.join(directory, "cert.pem"));
    server = createTransport({
      key: fs.readFileSync(path.join(directory, "key.pem")),
      cert,
      token,
      upstreamPort: upstream.address().port,
    });
    server.listen(0, "127.0.0.1");
    await once(server, "listening");
    const request = (authorization) =>
      new Promise((resolve, reject) => {
        const req = https.request({
          host: "127.0.0.1",
          servername: "localhost",
          port: server.address().port,
          ca: cert,
          headers: {
            Connection: "Upgrade",
            Upgrade: "websocket",
            "Sec-WebSocket-Version": "13",
            "Sec-WebSocket-Key": randomBytes(16).toString("base64"),
            ...(authorization ? { Authorization: authorization } : {}),
          },
        });
        req.on("error", reject);
        req.on("response", (response) => {
          response.resume();
          resolve(response.statusCode);
        });
        req.on("upgrade", (response, socket) => {
          socket.destroy();
          resolve(response.statusCode);
        });
        req.end();
      });
    assert.equal(await request(), 401);
    assert.equal(await request("Bearer invalid"), 401);
    assert.equal(upgrades, 0);
    assert.equal(await request(`Bearer ${token}`), 101);
    assert.equal(upgrades, 1);
  } finally {
    server?.stop();
    upstream.close();
    fs.rmSync(directory, { recursive: true, force: true });
  }
});
