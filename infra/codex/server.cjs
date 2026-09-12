// Keep the transport private without coupling container network/PID namespaces.
// Codex's native control socket validates peer PIDs, which cannot cross containers.
const fs = require("node:fs");
const net = require("node:net");
const { spawn } = require("node:child_process");
const { createTransport } = require("./transport.cjs");

const socketPath = "/run/codex/app-server.sock";
const sockets = new Set();
process.umask(0o077);
fs.rmSync(socketPath, { force: true });
const child = spawn("codex", process.argv.slice(2), { stdio: "inherit" });
const tlsServer = process.env.DEVFEED_CODEX_TLS_CERT
  ? createTransport({
      cert: fs.readFileSync(process.env.DEVFEED_CODEX_TLS_CERT),
      key: fs.readFileSync(process.env.DEVFEED_CODEX_TLS_KEY),
      token: process.env.DEVFEED_CODEX_AUTH_TOKEN,
    })
  : null;
if (tlsServer) tlsServer.listen(4501, "0.0.0.0");
const bridge = net.createServer((client) => {
  if (sockets.size >= 64) {
    client.destroy();
    return;
  }
  const upstream = net.connect(4500, "127.0.0.1");
  sockets.add(client);
  const close = () => {
    client.destroy();
    upstream.destroy();
    sockets.delete(client);
  };
  client.on("error", close);
  upstream.on("error", close);
  client.on("close", close);
  upstream.on("close", close);
  client.pipe(upstream).pipe(client);
});

function stop(signal = "SIGTERM") {
  tlsServer?.stop();
  bridge.close();
  for (const socket of sockets) socket.destroy();
  child.kill(signal);
}
bridge.on("error", () => {
  console.error("Codex socket bridge failed");
  stop();
});
bridge.listen(socketPath, () => fs.chmodSync(socketPath, 0o600));
for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, () => stop(signal));
child.on("error", () => {
  console.error("Codex failed to start");
  process.exit(1);
});
child.on("exit", (code) => {
  tlsServer?.stop();
  bridge.close();
  for (const socket of sockets) socket.destroy();
  fs.rmSync(socketPath, { force: true });
  process.exit(code ?? 1);
});
