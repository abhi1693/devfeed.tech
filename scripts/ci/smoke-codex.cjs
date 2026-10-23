// Native candidate-image contracts: shipped config, disposable analysis lifecycle,
// TLS/health probes and Fleet's mounted SQLite/init commands. No real model/auth.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const http = require("node:http");
const https = require("node:https");
const path = require("node:path");
const { once } = require("node:events");
const { randomBytes } = require("node:crypto");
const { spawn, spawnSync, execFileSync } = require("node:child_process");
const { DatabaseSync } = require("node:sqlite");

const pause = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));
const version = process.argv[2];
const home = process.env.CODEX_HOME;
const runtime = process.env.CODEX_SQLITE_HOME;
const retention = "/opt/codex-runtime/retention.cjs";
const heartbeat = path.join(runtime, ".retention-health");
const heartbeatProbe =
  "process.exit(Date.now() - Number(require('node:fs').readFileSync('/var/lib/codex-runtime/.retention-health', 'utf8')) < 90000 ? 0 : 1)";
const clients = new Set();
const children = new Set();
const deadline = setTimeout(() => {
  console.error("Codex native contracts exceeded 55 seconds");
  process.exit(1);
}, 55_000);

function start(command, args, options = {}) {
  const child = spawn(command, args, { stdio: ["ignore", "pipe", "pipe"], ...options });
  children.add(child);
  child.output = "";
  for (const stream of [child.stdout, child.stderr]) {
    stream.on("data", (data) => {
      child.output = (child.output + data).slice(-8000);
    });
  }
  return child;
}

async function stop(child) {
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  child.kill("SIGTERM");
  await Promise.race([once(child, "exit"), pause(2000)]);
  if (child.exitCode === null) child.kill("SIGKILL");
}

async function eventually(check, message, milliseconds = 5000) {
  const end = Date.now() + milliseconds;
  do {
    if (await check()) return;
    await pause(50);
  } while (Date.now() < end);
  assert.fail(message);
}

async function connection() {
  let socket;
  await eventually(
    async () => {
      socket = new WebSocket("ws://127.0.0.1:4500");
      return new Promise((resolve) => {
        socket.addEventListener("open", () => resolve(true), { once: true });
        socket.addEventListener("error", () => resolve(false), { once: true });
      });
    },
    "Codex app-server did not listen",
    10_000,
  );
  let identifier = 1;
  const pending = new Map();
  const notifications = [];
  socket.addEventListener("message", ({ data }) => {
    const message = JSON.parse(data);
    if (message.method) notifications.push(message);
    const request = pending.get(message.id);
    if (!request) return;
    clearTimeout(request.timeout);
    pending.delete(message.id);
    if (message.error) request.reject(new Error(`Codex RPC ${request.method} failed`));
    else request.resolve(message.result);
  });
  const client = {
    notifications,
    async request(method, params = {}) {
      const id = identifier++;
      return new Promise((resolve, reject) => {
        const timeout = setTimeout(() => {
          pending.delete(id);
          reject(new Error(`Codex RPC ${method} timed out`));
        }, 5000);
        pending.set(id, { resolve, reject, timeout, method });
        socket.send(JSON.stringify({ id, method, params }));
      });
    },
    async close() {
      clients.delete(client);
      socket.close();
      await Promise.race([once(socket, "close"), pause(500)]);
      for (const request of pending.values()) clearTimeout(request.timeout);
      pending.clear();
    },
  };
  clients.add(client);
  await client.request("initialize", {
    clientInfo: { name: "devfeed-native-ci", version },
    capabilities: { experimentalApi: true },
  });
  socket.send(JSON.stringify({ method: "initialized", params: {} }));
  return client;
}

async function verifyMountedCommands() {
  const preserved = {
    "auth.json": '{"fixture":"synthetic-not-a-login"}',
    "config.toml": "fixture = true\n",
    "state_5.sqlite": "synthetic unrelated state",
  };
  for (const [name, contents] of Object.entries(preserved)) {
    fs.writeFileSync(path.join(home, name), contents, { mode: 0o600 });
  }
  // Exact Fleet init command: existing sign-in bypasses interactive login.
  execFileSync(
    "sh",
    ["-ec", "if test -s /var/lib/codex/auth.json; then exit 0; fi; exec codex login --device-auth"],
    { timeout: 3000, stdio: "pipe" },
  );
  for (const suffix of ["", "-wal", "-shm"]) {
    fs.writeFileSync(path.join(home, `logs_2.sqlite${suffix}`), "obsolete diagnostic fixture");
  }
  execFileSync(process.execPath, [retention, "--cleanup-legacy", home], { stdio: "pipe" });
  for (const suffix of ["", "-wal", "-shm"]) {
    assert.equal(fs.existsSync(path.join(home, `logs_2.sqlite${suffix}`)), false);
  }
  for (const [name, contents] of Object.entries(preserved)) {
    assert.equal(fs.readFileSync(path.join(home, name), "utf8"), contents);
  }
  // Never let the app-server consume synthetic authentication or corrupt state.
  fs.unlinkSync(path.join(home, "auth.json"));
  fs.unlinkSync(path.join(home, "state_5.sqlite"));

  const filename = path.join(runtime, "logs_2.sqlite");
  const database = new DatabaseSync(filename);
  database.exec(
    "PRAGMA journal_mode=WAL; CREATE TABLE logs (id INTEGER PRIMARY KEY, ts INTEGER NOT NULL, level TEXT NOT NULL, feedback_log_body TEXT, estimated_bytes INTEGER NOT NULL DEFAULT 0)",
  );
  const now = Math.floor(Date.now() / 1000);
  const insert = database.prepare("INSERT INTO logs VALUES (?, ?, ?, ?, 0)");
  insert.run(1, now, "DEBUG", "verbose fixture");
  insert.run(2, now - 86401, "ERROR", "expired fixture");
  insert.run(3, now, "WARN", "retained fixture");
  let sidecar = start(process.execPath, [retention, runtime]);
  await eventually(() => fs.existsSync(heartbeat), "Retention CLI did not publish its heartbeat");
  assert.deepEqual(
    database
      .prepare("SELECT level FROM logs ORDER BY id")
      .all()
      .map((row) => row.level),
    ["WARN"],
  );
  assert.equal(spawnSync(process.execPath, ["-e", heartbeatProbe]).status, 0);
  await stop(sidecar);
  fs.writeFileSync(heartbeat, String(Date.now() - 90001));
  assert.notEqual(spawnSync(process.execPath, ["-e", heartbeatProbe]).status, 0);
  sidecar = start(process.execPath, [retention, runtime]);
  await eventually(
    () => Number(fs.readFileSync(heartbeat, "utf8")) > Date.now() - 5000,
    "Restarted retention CLI did not recover its stale heartbeat",
  );
  assert.equal(spawnSync(process.execPath, ["-e", heartbeatProbe]).status, 0);
  await stop(sidecar);
  database.close();
  // The fixture has its own minimal log schema; the native server creates its real schema next.
  for (const suffix of ["", "-wal", "-shm"]) fs.rmSync(`${filename}${suffix}`, { force: true });
  console.log("Native SQLite retention, fresh/stale/recovered probes and init commands passed");
}

function tlsUpgrade(authorization) {
  return new Promise((resolve, reject) => {
    const request = https.request({
      host: "127.0.0.1",
      servername: "localhost",
      port: 4501,
      ca: fs.readFileSync("/run/smoke-tls/cert.pem"),
      headers: {
        Connection: "Upgrade",
        Upgrade: "websocket",
        "Sec-WebSocket-Version": "13",
        "Sec-WebSocket-Key": randomBytes(16).toString("base64"),
        ...(authorization ? { Authorization: authorization } : {}),
      },
      timeout: 3000,
    });
    request.on("error", reject);
    request.on("timeout", () => request.destroy(new Error("TLS probe timed out")));
    request.on("response", (response) => {
      response.resume();
      resolve(response.statusCode);
    });
    request.on("upgrade", (response, socket) => {
      socket.destroy();
      resolve(response.statusCode);
    });
    request.end();
  });
}

async function main() {
  assert.equal(process.getuid(), 10001);
  assert.equal(process.getgid(), 10001);
  assert.equal(home, "/var/lib/codex");
  assert.equal(runtime, "/var/lib/codex-runtime");
  assert.throws(() => fs.writeFileSync("/opt/devfeed/smoke-must-not-write", "x"), {
    code: "EROFS",
  });
  await verifyMountedCommands();

  let responseStream;
  let modelRequests = 0;
  const sendEvent = (event) =>
    responseStream.write(`event: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`);
  const completeModelResponse = () => {
    sendEvent({
      type: "response.output_item.done",
      item: {
        type: "message",
        role: "assistant",
        id: "native-fixture-message",
        content: [{ type: "output_text", text: "Done" }],
      },
    });
    sendEvent({
      type: "response.completed",
      response: {
        id: "native-fixture-response",
        usage: { input_tokens: 0, output_tokens: 0, total_tokens: 0 },
      },
    });
    responseStream.end();
  };
  const model = http.createServer((request, response) => {
    request.resume();
    if (request.method === "POST" && request.url === "/v1/responses") {
      modelRequests++;
      responseStream = response;
      response.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-cache" });
      sendEvent({ type: "response.created", response: { id: "native-fixture-response" } });
    } else if (request.url === "/v1/models") {
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end('{"object":"list","data":[]}');
    } else {
      response.writeHead(426);
      response.end();
    }
  });
  model.listen(0, "127.0.0.1");
  await once(model, "listening");
  try {
    const token = "synthetic-native-smoke-service-token-minimum-32-characters";
    // Run the SHIPPED entrypoint and production flags. Model overrides point only
    // to this in-container fixture; never override the thread-unload setting.
    const server = start(
      "sh",
      [
        "/opt/devfeed/entrypoint.sh",
        "app-server",
        "--listen",
        "ws://127.0.0.1:4500",
        "-c",
        'history.persistence="none"',
        "-c",
        'model="mock-model"',
        "-c",
        'model_provider="native_smoke"',
        "-c",
        'model_providers.native_smoke.name="Isolated native smoke"',
        "-c",
        `model_providers.native_smoke.base_url="http://127.0.0.1:${model.address().port}/v1"`,
        "-c",
        'model_providers.native_smoke.wire_api="responses"',
        "-c",
        "model_providers.native_smoke.requires_openai_auth=false",
        "-c",
        "model_providers.native_smoke.supports_websockets=false",
        "-c",
        "model_providers.native_smoke.request_max_retries=0",
        "-c",
        "model_providers.native_smoke.stream_max_retries=0",
      ],
      {
        env: {
          ...process.env,
          DEVFEED_CODEX_TLS_CERT: "/run/smoke-tls/cert.pem",
          DEVFEED_CODEX_TLS_KEY: "/run/smoke-tls/key.pem",
          DEVFEED_CODEX_AUTH_TOKEN: token,
          RUST_LOG: "warn",
        },
      },
    );
    try {
      const client = await connection();
      assert.equal(fs.statSync("/run/codex/app-server.sock").mode & 0o777, 0o600);
      assert.equal(fs.statSync(path.join(home, "config.toml")).mode & 0o777, 0o600);
      execFileSync(process.execPath, ["/opt/devfeed/health.cjs"], { timeout: 5000 });
      const configuration = await client.request("config/read", { includeLayers: false });
      assert.equal(
        configuration.config.thread_unload_delay_secs,
        0,
        "Shipped idle cleanup is missing",
      );
      const account = await client.request("account/read", { refreshToken: false });
      assert.equal(typeof account.requiresOpenaiAuth, "boolean");
      assert.equal(account.account, null, "Native smoke must not have operator credentials");
      assert.equal(await tlsUpgrade(), 401);
      assert.equal(await tlsUpgrade("Bearer invalid"), 401);
      assert.equal(await tlsUpgrade(`Bearer ${token}`), 101);

      const startThread = async (owner) => {
        const profile = "devfeed-native-smoke";
        const thread = await owner.request("thread/start", {
          model: "mock-model",
          ephemeral: true,
          approvalPolicy: "never",
          permissions: profile,
          config: {
            project_doc_max_bytes: 0,
            permissions: {
              [profile]: { filesystem: { "/": "deny" }, network: { enabled: false } },
            },
            features: { code_mode_host: false },
          },
        });
        assert.equal(thread.activePermissionProfile.id, profile);
        assert.equal(thread.activePermissionProfile.extends, null);
        return thread.thread.id;
      };
      const loaded = async (id) => (await client.request("thread/loaded/list")).data.includes(id);
      const idle = await startThread(client);
      assert.equal(await loaded(idle), true);
      assert.equal(
        (await client.request("thread/unsubscribe", { threadId: idle })).status,
        "unsubscribed",
      );
      await eventually(async () => !(await loaded(idle)), "Idle unsubscribed thread stayed loaded");

      const disconnected = await connection();
      const orphan = await startThread(disconnected);
      await disconnected.close();
      await eventually(
        async () => !(await loaded(orphan)),
        "Disconnected client's thread stayed loaded",
      );

      const active = await startThread(client);
      const turn = await client.request("turn/start", {
        threadId: active,
        input: [{ type: "text", text: "Return the disposable fixture answer" }],
      });
      assert.equal(turn.turn.status, "inProgress");
      await eventually(() => !!responseStream, "Mock model did not receive the active turn");
      assert.equal(
        (await client.request("thread/unsubscribe", { threadId: active })).status,
        "unsubscribed",
      );
      await pause(1250);
      assert.equal(await loaded(active), true, "Idle cleanup evicted an active turn");
      assert.equal(responseStream.destroyed, false, "Idle cleanup aborted the model stream");
      completeModelResponse();
      await eventually(
        async () => !(await loaded(active)),
        "Completed analysis thread stayed loaded",
      );
      // Independently prove the same mock stream finishes successfully. Keep this
      // thread subscribed so a malformed/error completion cannot pass as unloading.
      responseStream = undefined;
      const successful = await startThread(client);
      const successfulTurn = await client.request("turn/start", {
        threadId: successful,
        input: [{ type: "text", text: "Return the disposable fixture answer" }],
      });
      await eventually(() => !!responseStream, "Mock model did not receive the successful turn");
      completeModelResponse();
      const completion = () =>
        client.notifications.find(
          (message) =>
            message.method === "turn/completed" &&
            message.params.threadId === successful &&
            message.params.turn.id === successfulTurn.turn.id,
        );
      await eventually(() => !!completion(), "Subscribed turn did not report completion");
      assert.equal(completion().params.turn.status, "completed", "Mock turn failed");
      const output = client.notifications.find(
        (message) =>
          message.method === "item/completed" &&
          message.params.threadId === successful &&
          message.params.turnId === successfulTurn.turn.id &&
          message.params.item.type === "agentMessage",
      );
      assert.equal(output?.params.item.text, "Done", "Mock assistant output was not delivered");
      assert.equal(await loaded(successful), true);
      assert.equal(
        (await client.request("thread/unsubscribe", { threadId: successful })).status,
        "unsubscribed",
      );
      await eventually(
        async () => !(await loaded(successful)),
        "Successfully completed analysis thread stayed loaded",
      );
      assert.equal(modelRequests, 2);
      console.log(
        "Shipped entrypoint/config, TLS/health, idle/disconnect cleanup, successful output and active-turn safety passed",
      );
    } catch (error) {
      console.error(server.output);
      throw error;
    } finally {
      await stop(server);
    }
  } finally {
    responseStream?.destroy();
    model.closeAllConnections();
    await new Promise((resolve) => model.close(resolve));
  }
}

main()
  .catch((error) => {
    console.error(error.message);
    process.exitCode = 1;
  })
  .finally(async () => {
    for (const client of clients) await client.close();
    await Promise.all([...children].map(stop));
    clearTimeout(deadline);
  });
