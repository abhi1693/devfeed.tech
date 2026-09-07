const net = require('node:net');
const timeout = setTimeout(() => process.exit(1), 3000);
fetch('http://127.0.0.1:4500/readyz', { signal: AbortSignal.timeout(2500) })
  .then(response => {
    if (!response.ok) process.exit(1);
    const socket = net.createConnection('/run/codex/app-server.sock');
    socket.once('connect', () => { socket.destroy(); clearTimeout(timeout); process.exit(0); });
    socket.once('error', () => process.exit(1));
  })
  .catch(() => process.exit(1));
