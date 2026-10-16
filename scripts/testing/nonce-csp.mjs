import assert from "node:assert/strict";
import { JSDOM } from "jsdom";

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
      // CSP strict-dynamic permits a nonced bootstrap to load chunks without another nonce.
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
