import assert from "node:assert/strict";
import { JSDOM } from "jsdom";

// Google's service frame emits this non-enforcing ancestor report with no console source location.
const preferredSourceReportOnly =
  "Framing 'https://news.google.com/' violates the following report-only Content Security Policy directive: \"frame-ancestors 'self'\". The violation has been logged, but no further action has been taken.";

/** Keep enforced/application policy failures fatal and retain the exact vendor-only report. */
export function collectReaderPageErrors(page) {
  const errors = [];
  const preferredSourceReports = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    const text = message.text();
    if (
      !/Content Security Policy/.test(text) &&
      !(message.type() === "error" && /Refused to/.test(text))
    )
      return;
    if (text.trimEnd() === preferredSourceReportOnly)
      preferredSourceReports.push({
        message: text,
        location: message.location(),
        page: page.url(),
      });
    else errors.push(text);
  });
  return { errors, preferredSourceReports };
}

// Exercise real production HTML, including framework and inline bootstrap scripts.
export async function checkNonceCsp(context, url) {
  const page = await context.newPage();
  const consoleViolations = [];
  page.on("console", (message) => {
    if (message.type() === "error" && /content security policy/i.test(message.text()))
      consoleViolations.push(message.text());
  });
  await page.addInitScript(() => {
    window.__nonceCspViolations = [];
    document.addEventListener("securitypolicyviolation", (event) => {
      window.__nonceCspViolations.push(`${event.effectiveDirective}: ${event.blockedURI}`);
    });
  });
  try {
    const nonces = [];
    for (let visit = 0; visit < 2; visit++) {
      const response = await page.goto(url);
      assert.equal(response.status(), 200);
      const csp = response.headers()["content-security-policy"];
      const nonce = csp?.match(/script-src [^;]*'nonce-([^']+)'/)?.[1];
      assert.ok(nonce, "HTML response must have a script nonce");
      assert.doesNotMatch(csp, /script-src [^;]*'unsafe-inline'/);
      assert.match(csp, /script-src [^;]*'strict-dynamic'/);
      // Inspect this exact response, before hydration appends trusted script descendants.
      // The script-element policy permits framework assets; inline bootstrap still needs a nonce.
      // JSDOM's default parser neither executes scripts nor fetches resources.
      const initial = new JSDOM(await response.text());
      try {
        const scripts = [...initial.window.document.querySelectorAll("script")].filter((element) =>
          ["", "module", "text/javascript", "application/javascript"].includes(element.type),
        );
        assert.ok(scripts.length > 0, "Page must render executable scripts");
        for (const script of scripts)
          assert.equal(
            script.getAttribute("nonce"),
            nonce,
            `Server-rendered script must use this response's nonce: ${script.getAttribute("src") || "inline"}`,
          );
      } finally {
        initial.window.close();
      }
      await page.evaluate(
        () => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))),
      );
      assert.deepEqual(await page.evaluate(() => window.__nonceCspViolations), []);
      assert.deepEqual(consoleViolations, [], "Browser must enforce CSP without script violations");
      nonces.push(nonce);
    }
    assert.notEqual(nonces[0], nonces[1], "Nonce must change between page loads");
  } finally {
    await page.close();
  }
}

// Reproduce a CDN adding unnonced parser-inserted scripts after origin rendering.
export async function checkCloudflareScriptsCsp(context, url) {
  const page = await context.newPage();
  const origin = new URL(url).origin;
  await page.addInitScript(() => {
    window.__bridgeCspViolations = [];
    document.addEventListener("securitypolicyviolation", (event) => {
      window.__bridgeCspViolations.push(event.blockedURI);
    });
  });
  await page.route(`${origin}/.webmcp/bridge.js`, (route) =>
    route.fulfill({ contentType: "text/javascript", body: "window.__bridgeLoaded = true" }),
  );
  const rocketPath = "/cdn-cgi/scripts/7d0fa10a/cloudflare-static/rocket-loader.min.js";
  await page.route(`${origin}${rocketPath}`, (route) =>
    route.fulfill({ contentType: "text/javascript", body: "window.__rocketLoaded = true" }),
  );
  await page.route(`${origin}/cdn-cgi/scripts-extra/rocket-loader.min.js`, (route) =>
    route.fulfill({
      contentType: "text/javascript",
      body: "window.__rocketLookalikeLoaded = true",
    }),
  );
  const rotatedRocketPath = "/cdn-cgi/scripts/abcdef12/cloudflare-static/rocket-loader.min.js";
  await page.route(`${origin}${rotatedRocketPath}`, (route) =>
    route.fulfill({ contentType: "text/javascript", body: "window.__rotatedRocketLoaded = true" }),
  );
  await page.route(`${origin}/untrusted.js`, (route) =>
    route.fulfill({ contentType: "text/javascript", body: "window.__untrustedLoaded = true" }),
  );
  await page.route(`${origin}/.webmcp/bridge.js.extra`, (route) =>
    route.fulfill({ contentType: "text/javascript", body: "window.__lookalikeLoaded = true" }),
  );
  for (const pattern of [
    "https://www.googletagmanager.com/**",
    "https://scripts.clarity.ms/**",
    `${origin}/_next/static/csp-probe.js`,
  ])
    await page.route(pattern, (route) =>
      route.fulfill({
        contentType: "text/javascript",
        body: "window.__allowedScripts = (window.__allowedScripts || 0) + 1",
      }),
    );
  try {
    for (const originalPolicy of [true, false]) {
      await page.route(url, async (route) => {
        const response = await route.fetch();
        const headers = response.headers();
        if (originalPolicy)
          headers["content-security-policy"] = headers["content-security-policy"]
            .split("; ")
            .filter((directive) => !directive.startsWith("script-src-elem "))
            .join("; ");
        const body = (await response.text()).replace(
          "<head>",
          `<head><script type="module" src="${origin}/.webmcp/bridge.js" data-packs="mcp-server-client"></script><script src="${origin}${rocketPath}"></script><script src="${origin}${rotatedRocketPath}"></script><script src="${origin}/cdn-cgi/scripts-extra/rocket-loader.min.js"></script><script src="${origin}/untrusted.js"></script><script src="${origin}/.webmcp/bridge.js.extra"></script><script>window.__untrustedInline = true</script>`,
        );
        await route.fulfill({ response, headers, body });
      });
      await page.goto(url);
      if (originalPolicy)
        await page.waitForFunction(() =>
          window.__bridgeCspViolations.some((uri) => uri.endsWith("/.webmcp/bridge.js")),
        );
      else
        await page.waitForFunction(
          () =>
            window.__bridgeLoaded === true &&
            window.__rocketLoaded === true &&
            window.__rotatedRocketLoaded === true,
        );
      if (originalPolicy)
        await page.waitForFunction(
          (path) => window.__bridgeCspViolations.some((uri) => uri.endsWith(path)),
          rocketPath,
        );
      await page.waitForFunction(() => window.__bridgeCspViolations.includes("inline"));
      const state = await page.evaluate(() => ({
        bridge: !!window.__bridgeLoaded,
        rocket: !!window.__rocketLoaded,
        rotatedRocket: !!window.__rotatedRocketLoaded,
        rocketLookalike: !!window.__rocketLookalikeLoaded,
        untrusted: !!window.__untrustedLoaded,
        lookalike: !!window.__lookalikeLoaded,
        inline: !!window.__untrustedInline,
        violations: window.__bridgeCspViolations,
      }));
      assert.equal(state.bridge, !originalPolicy);
      assert.equal(state.rocket, !originalPolicy);
      assert.equal(state.rotatedRocket, !originalPolicy);
      assert.equal(state.rocketLookalike, false);
      assert.equal(state.untrusted, false);
      assert.equal(state.lookalike, false);
      assert.equal(state.inline, false);
      if (!originalPolicy) {
        assert.ok(!state.violations.some((uri) => uri.endsWith("/.webmcp/bridge.js")));
        assert.ok(!state.violations.some((uri) => uri.endsWith(rocketPath)));
      }
      if (!originalPolicy) {
        await page.evaluate(async (origin) => {
          await Promise.all(
            [
              "https://www.googletagmanager.com/gtag/js?id=G-CSPTEST",
              "https://scripts.clarity.ms/probe.js",
              `${origin}/_next/static/csp-probe.js`,
            ].map(
              (src) =>
                new Promise((resolve, reject) => {
                  const script = document.createElement("script");
                  script.src = src;
                  script.onload = resolve;
                  script.onerror = reject;
                  document.head.appendChild(script);
                }),
            ),
          );
        }, origin);
        assert.equal(await page.evaluate(() => window.__allowedScripts), 3);
      }
      await page.unroute(url);
    }
  } finally {
    await page.close();
  }
}
