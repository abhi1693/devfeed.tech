// Check the same app-server handshake/account contract used by backend readiness.
// No sign-in, model request or credential refresh is performed.
const assert = require("node:assert/strict");
const fs = require("node:fs");

assert.notEqual(process.getuid(), 0, "Codex must run without root privileges");
assert.equal(fs.statSync("/run/codex/app-server.sock").mode & 0o777, 0o600);

const socket = new WebSocket("ws://127.0.0.1:4500");
let completed = false;
const timer = setTimeout(() => {
  console.error("Codex app-server protocol check timed out");
  process.exit(1);
}, 5_000);
socket.addEventListener("error", () => {
  if (completed) return;
  console.error("Codex app-server connection failed");
  process.exit(1);
});
socket.addEventListener("open", () => {
  socket.send(
    JSON.stringify({
      id: 1,
      method: "initialize",
      params: { clientInfo: { name: "devfeed-ci", version: process.argv[2] } },
    }),
  );
});
socket.addEventListener("message", ({ data }) => {
  const response = JSON.parse(data);
  if (response.id !== 1 && response.id !== 2) return;
  assert.equal(response.error, undefined, "Codex readiness request failed");
  assert.ok(response.result && typeof response.result === "object");
  if (response.id === 1) {
    socket.send(JSON.stringify({ method: "initialized", params: {} }));
    socket.send(JSON.stringify({ id: 2, method: "account/read", params: { refreshToken: false } }));
  } else {
    assert.equal(typeof response.result.requiresOpenaiAuth, "boolean");
    assert.ok(response.result.account === null || typeof response.result.account === "object");
    completed = true;
    clearTimeout(timer);
    socket.close();
    console.log("Codex initialize and account/read passed");
  }
});
