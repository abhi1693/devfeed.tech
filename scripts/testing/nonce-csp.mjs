import assert from "node:assert/strict";

// Exercise real production HTML, including framework and inline bootstrap scripts.
export async function checkNonceCsp(context, url) {
  const page = await context.newPage();
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
      const scripts = await page
        .locator("script")
        .evaluateAll((elements) =>
          elements
            .filter((element) =>
              ["", "module", "text/javascript", "application/javascript"].includes(element.type),
            )
            .map((element) => ({ src: element.src || "inline", nonce: element.nonce })),
        );
      assert.ok(scripts.length > 0, "Page must render executable scripts");
      for (const script of scripts)
        assert.equal(script.nonce, nonce, `Script must use this response's nonce: ${script.src}`);
      assert.deepEqual(await page.evaluate(() => window.__nonceCspViolations), []);
      nonces.push(nonce);
    }
    assert.notEqual(nonces[0], nonces[1], "Nonce must change between page loads");
  } finally {
    await page.close();
  }
}
