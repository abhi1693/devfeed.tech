const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const https = require('node:https');
const { execFileSync } = require('node:child_process');
const { once } = require('node:events');
const { createTransport } = require('./transport.cjs');

test('TLS transport authenticates before connecting and strips its token', async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'devfeed-tls-test-'));
  let upgrades = 0;
  const upstream = http.createServer();
  const token = 'test-service-token-with-at-least-32-characters';
  let server;
  try {
    execFileSync('openssl', ['req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
      '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost',
      '-keyout', path.join(directory, 'key.pem'), '-out', path.join(directory, 'cert.pem')], { stdio: 'ignore' });
    upstream.on('upgrade', (request, socket) => {
      upgrades++;
      assert.equal(request.headers.authorization, undefined);
      socket.end('HTTP/1.1 101 Switching Protocols\r\nConnection: Upgrade\r\nUpgrade: websocket\r\n\r\n');
    });
    upstream.listen(0, '127.0.0.1');
    await once(upstream, 'listening');
    const cert = fs.readFileSync(path.join(directory, 'cert.pem'));
    server = createTransport({ key: fs.readFileSync(path.join(directory, 'key.pem')), cert, token,
      upstreamPort: upstream.address().port });
    server.listen(0, '127.0.0.1');
    await once(server, 'listening');
    const request = authorization => new Promise((resolve, reject) => {
      const req = https.request({ host: '127.0.0.1', servername: 'localhost',
        port: server.address().port, ca: cert, headers: {
          Connection: 'Upgrade', Upgrade: 'websocket', 'Sec-WebSocket-Version': '13',
          'Sec-WebSocket-Key': 'dGhlIHNhbXBsZSBub25jZQ==', ...(authorization ? { Authorization: authorization } : {}),
        } });
      req.on('error', reject);
      req.on('response', response => { response.resume(); resolve(response.statusCode); });
      req.on('upgrade', (response, socket) => { socket.destroy(); resolve(response.statusCode); });
      req.end();
    });
    assert.equal(await request(), 401);
    assert.equal(await request('Bearer invalid'), 401);
    assert.equal(upgrades, 0);
    assert.equal(await request(`Bearer ${token}`), 101);
    assert.equal(upgrades, 1);
  } finally {
    server?.stop();
    upstream.close();
    fs.rmSync(directory, { recursive: true, force: true });
  }
});
