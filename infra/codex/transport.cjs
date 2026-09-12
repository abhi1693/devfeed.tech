// Optional TLS transport for private cluster clients. Model credentials never
// leave the Codex container; callers authenticate with a separate service token.
const https = require("node:https");
const net = require("node:net");
const { timingSafeEqual } = require("node:crypto");

function createTransport({ key, cert, token, upstreamPort = 4500 }) {
  if (!token || token.length < 32) throw new Error("Codex transport requires a strong token");
  const expected = Buffer.from(`Bearer ${token}`);
  const sockets = new Set();
  const server = https.createServer({ key, cert, minVersion: "TLSv1.2" }, (_request, response) => {
    response.writeHead(404).end();
  });
  server.on("upgrade", (request, socket, head) => {
    const supplied = Buffer.from(request.headers.authorization || "");
    if (supplied.length !== expected.length || !timingSafeEqual(supplied, expected)) {
      socket.end("HTTP/1.1 401 Unauthorized\r\nConnection: close\r\n\r\n");
      return;
    }
    if (request.url !== "/" || sockets.size >= 64) {
      socket.end("HTTP/1.1 503 Service Unavailable\r\nConnection: close\r\n\r\n");
      return;
    }
    const upstream = net.connect(upstreamPort, "127.0.0.1");
    sockets.add(socket);
    const close = () => {
      socket.destroy();
      upstream.destroy();
      sockets.delete(socket);
    };
    socket.on("error", close);
    upstream.on("error", close);
    socket.on("close", close);
    upstream.on("close", close);
    upstream.once("connect", () => {
      // Rebuild only the WebSocket handshake; never forward service credentials.
      upstream.write(
        [
          "GET / HTTP/1.1",
          `Host: 127.0.0.1:${upstreamPort}`,
          "Upgrade: websocket",
          "Connection: Upgrade",
          `Sec-WebSocket-Key: ${request.headers["sec-websocket-key"] || ""}`,
          "Sec-WebSocket-Version: 13",
          "",
          "",
        ].join("\r\n"),
      );
      if (head.length) upstream.write(head);
      socket.pipe(upstream).pipe(socket);
    });
  });
  server.stop = () => {
    server.close();
    for (const socket of sockets) socket.destroy();
  };
  return server;
}

module.exports = { createTransport };
