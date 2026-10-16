import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { fileURLToPath } from "node:url";
import { signInResponse } from "./sign-in.mjs";

/** The production Next login page with isolated upstreams and independent analytics flags. */
export async function acquisitionLoginServer(t) {
  const root = fileURLToPath(new URL("../../", import.meta.url));
  const logins = [];
  const upstream = createServer(async (request, response) => {
    const path = new URL(request.url, "http://localhost").pathname;
    if (path === "/v1/user/auth/config") {
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end(JSON.stringify({ enabled: true, providers: [] }));
    } else if (path === "/v1/user/auth/login") {
      logins.push({ cookie: request.headers.cookie, path: request.url });
      const result = await signInResponse(
        request.url,
        `http://127.0.0.1:${upstream.address().port}/authorize`,
      );
      response.writeHead(result.status, result.headers);
      response.end(result.body);
    } else if (path === "/authorize") {
      response.writeHead(200, { "Content-Type": "text/html" });
      response.end("<p>Sign-in provider</p>");
    } else {
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end(path === "/v1/user/auth/me" ? "null" : "{}");
    }
  });
  await new Promise((resolve) => upstream.listen(0, "127.0.0.1", resolve));
  const probe = createServer();
  await new Promise((resolve) => probe.listen(0, "127.0.0.1", resolve));
  const port = probe.address().port;
  await new Promise((resolve) => probe.close(resolve));
  const origin = `http://127.0.0.1:${port}`;
  const inherited = Object.fromEntries(
    Object.entries(process.env).filter(([name]) => !name.startsWith("DEVFEED_")),
  );
  const app = spawn(
    process.execPath,
    [
      `${root}/node_modules/next/dist/bin/next`,
      "start",
      "--hostname",
      "127.0.0.1",
      "--port",
      String(port),
    ],
    {
      cwd: `${root}/apps/web`,
      env: {
        ...inherited,
        DEVFEED_USER_BASE_URL: origin,
        DEVFEED_PUBLIC_API_URL: `http://127.0.0.1:${upstream.address().port}`,
        DEVFEED_USER_API_URL: `http://127.0.0.1:${upstream.address().port}`,
        DEVFEED_ANALYTICS_ENABLED: "false",
        DEVFEED_EXTENSION_ANALYTICS_ENABLED: "true",
        DEVFEED_READER_MEASUREMENT_ENABLED: "true",
        DEVFEED_READER_ACQUISITION_CAMPAIGNS: JSON.stringify(["organic-launch"]),
      },
      stdio: "pipe",
    },
  );
  let logs = "";
  app.stdout.on("data", (value) => {
    logs += value;
  });
  app.stderr.on("data", (value) => {
    logs += value;
  });
  t.after(async () => {
    if (app.exitCode === null && app.signalCode === null) {
      const stopped = new Promise((resolve) => app.once("exit", resolve));
      const deadline = setTimeout(() => app.kill("SIGKILL"), 5000);
      app.kill("SIGTERM");
      await stopped;
      clearTimeout(deadline);
    }
    upstream.closeAllConnections();
    await new Promise((resolve) => upstream.close(resolve));
  });
  let ready = false;
  for (let attempt = 0; attempt < 100; attempt++) {
    try {
      ready = (await fetch(`${origin}/login`)).ok;
      if (ready) break;
    } catch {
      /* Wait for the isolated Next process. */
    }
    if (app.exitCode !== null) break;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  assert.ok(ready, `Acquisition login reader did not become ready: ${logs}`);
  return { origin, logins };
}
