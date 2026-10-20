import assert from "node:assert/strict";
import { spawn, execFileSync } from "node:child_process";
import { createServer as createHttpsServer } from "node:https";
import { createServer } from "node:net";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { chromium } from "playwright";

const root = path.resolve(import.meta.dirname, "../..");
async function port() {
  const server = createServer();
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const result = server.address().port;
  await new Promise((resolve) => server.close(resolve));
  return result;
}
async function ready(url, child, logs) {
  for (let attempt = 0; attempt < 120; attempt++) {
    if (child.exitCode !== null) throw new Error(logs());
    try {
      await fetch(url);
      return;
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`Fixture did not start: ${logs()}`);
}
async function stop(child) {
  if (!child || child.exitCode !== null) return;
  const done = new Promise((resolve) => child.once("exit", resolve));
  child.kill("SIGTERM");
  const timer = setTimeout(() => child.kill("SIGKILL"), 5000);
  await done;
  clearTimeout(timer);
}

export async function checkLoginRecovery(t, browserName = null) {
  const temporary = await mkdtemp(path.join(tmpdir(), "devfeed-login-recovery-"));
  const apiPort = await port(),
    webPort = await port();
  let api, web, proxy, context, browser;
  let logs = "";
  let authorizations = 0;
  const secrets = [];
  t.after(async () => {
    await context?.close();
    await browser?.close();
    await stop(web);
    await stop(api);
    if (proxy) {
      proxy.closeAllConnections();
      await new Promise((resolve) => proxy.close(resolve));
    }
    await rm(temporary, { recursive: true, force: true });
  });
  const env = Object.fromEntries(
    Object.entries(process.env).filter(([key]) => !key.startsWith("DEVFEED_")),
  );
  api = spawn(
    "uv",
    ["run", "--locked", "python", "scripts/testing/login-recovery.py", String(apiPort)],
    {
      cwd: root,
      env: {
        ...env,
        DEVFEED_DATABASE_URL: "postgresql+psycopg://ci@database.invalid/ci",
        DEVFEED_REDIS_URL: "redis://redis.invalid/15",
      },
      stdio: "pipe",
    },
  );
  for (const stream of [api.stdout, api.stderr]) stream.on("data", (chunk) => (logs += chunk));
  await ready(`http://127.0.0.1:${apiPort}/v1/user/auth/config`, api, () => logs);
  web = spawn(
    process.execPath,
    [
      path.join(root, "node_modules/next/dist/bin/next"),
      "start",
      "--hostname",
      "127.0.0.1",
      "--port",
      String(webPort),
    ],
    {
      cwd: path.join(root, "apps/web"),
      env: {
        ...env,
        DEVFEED_ANALYTICS_ENABLED: "false",
        DEVFEED_USER_BASE_URL: "https://devfeed.tech",
        DEVFEED_USER_API_URL: `http://127.0.0.1:${apiPort}`,
        DEVFEED_PUBLIC_API_URL: `http://127.0.0.1:${apiPort}`,
      },
      stdio: "pipe",
    },
  );
  for (const stream of [web.stdout, web.stderr]) stream.on("data", (chunk) => (logs += chunk));
  await ready(`http://127.0.0.1:${webPort}/login`, web, () => logs);
  execFileSync(
    "openssl",
    [
      "req",
      "-x509",
      "-newkey",
      "rsa:2048",
      "-nodes",
      "-keyout",
      path.join(temporary, "key.pem"),
      "-out",
      path.join(temporary, "cert.pem"),
      "-days",
      "1",
      "-subj",
      "/CN=devfeed.tech",
    ],
    { stdio: "ignore" },
  );
  proxy = createHttpsServer(
    {
      key: await readFile(path.join(temporary, "key.pem")),
      cert: await readFile(path.join(temporary, "cert.pem")),
    },
    async (req, res) => {
      try {
        const requested = new URL(req.url, "https://devfeed.tech");
        if (requested.pathname === "/test-provider/authorize") {
          const params = requested.searchParams;
          secrets.push([params.get("state"), params.get("nonce"), params.get("code_challenge")]);
          authorizations++;
          const callback = new URL("https://devfeed.tech/api/v1/user/auth/callback");
          callback.searchParams.set("state", params.get("state"));
          callback.searchParams.set(
            authorizations === 1 ? "error" : "code",
            authorizations === 1 ? "access_denied" : "fresh-browser-code",
          );
          res.writeHead(302, { Location: callback.href });
          res.end();
          return;
        }
        const response = await fetch(`http://127.0.0.1:${webPort}${req.url}`, {
          redirect: "manual",
          headers: { ...req.headers, host: "devfeed.tech" },
        });
        const headers = Object.fromEntries(response.headers);
        delete headers["content-encoding"];
        delete headers["content-length"];
        headers["set-cookie"] = response.headers.getSetCookie();
        res.writeHead(response.status, headers);
        res.end(Buffer.from(await response.arrayBuffer()));
      } catch {
        res.writeHead(502);
        res.end("Login recovery fixture unavailable");
      }
    },
  );
  await new Promise((resolve) => proxy.listen(0, "127.0.0.1", resolve));
  const args = [
    `--host-resolver-rules=MAP devfeed.tech 127.0.0.1:${proxy.address().port}`,
    "--no-proxy-server",
    "--ignore-certificate-errors",
  ];
  const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH;
  if (browserName) {
    const extension = path.join(root, `apps/extensions/dist/${browserName}`);
    context = await chromium.launchPersistentContext(path.join(temporary, "profile"), {
      executablePath,
      channel: executablePath ? undefined : browserName === "edge" ? "msedge" : "chromium",
      headless: true,
      args: [...args, `--disable-extensions-except=${extension}`, `--load-extension=${extension}`],
    });
  } else {
    browser = await chromium.launch({ executablePath, headless: true, args });
    context = await browser.newContext({ ignoreHTTPSErrors: true });
  }
  let page = await context.newPage();
  const destination = browserName ? "/extension/login-complete" : "/read-later";
  await context.route("https://devfeed.tech/api/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.startsWith("/api/v1/user/auth/")) return route.continue();
    const fixtures = {
      "/api/v1/feed/options": {
        sources: [],
        content_types: ["article", "news"],
        languages: ["en"],
      },
      "/api/v1/leaderboard": { longest_streak: [], reading_days: [] },
      "/api/v1/extension/analytics": { enabled: false },
      "/api/v1/user/engagement": [],
    };
    return route.fulfill({ json: fixtures[url.pathname] ?? { items: [], next_cursor: null } });
  });
  if (browserName) {
    await page.goto(browserName === "edge" ? "edge://newtab" : "chrome://newtab");
    await page.waitForURL((url) => url.protocol === "chrome-extension:");
    const signIn = page.getByRole("link", { name: "Sign in", exact: true }).first();
    await signIn.waitFor();
    assert.equal(new URL(await signIn.getAttribute("href")).origin, "https://devfeed.tech");
    const opened = context.waitForEvent("page");
    await signIn.click();
    page = await opened;
  } else await page.goto(`https://devfeed.tech/login?return_to=${encodeURIComponent(destination)}`);
  await page.getByRole("link", { name: "Continue to sign in" }).click();
  try {
    await page
      .getByRole("alert")
      .filter({ hasText: "Sign-in was cancelled" })
      .waitFor({ timeout: 10000 });
  } catch (error) {
    console.error(
      "Recovery page:",
      new URL(page.url()).origin + new URL(page.url()).pathname,
      await page.locator("body").innerText(),
    );
    console.error(logs);
    throw error;
  }
  const retry = page.getByRole("link", { name: "Try sign-in again" });
  const retryUrl = new URL(await retry.getAttribute("href"), page.url());
  assert.ok(retryUrl.searchParams.get("retry"));
  assert.equal(retryUrl.searchParams.has("return_to"), false);
  await page.screenshot({
    path: path.join(root, `reports/login-recovery-${browserName ?? "web"}.png`),
  });
  if (browserName) {
    const closed = page.waitForEvent("close");
    await retry.click();
    await closed;
  } else {
    await retry.click();
    await page.waitForURL((url) => url.pathname === destination);
  }
  assert.equal(authorizations, 2);
  for (let i = 0; i < 3; i++) assert.notEqual(secrets[0][i], secrets[1][i]);
  assert.ok(
    (await context.cookies("https://devfeed.tech")).some(
      (cookie) => cookie.name === "__Host-devfeed_user_session",
    ),
  );
}
