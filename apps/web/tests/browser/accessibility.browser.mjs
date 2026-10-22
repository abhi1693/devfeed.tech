import assert from "node:assert/strict";
import test from "node:test";
import { randomBytes } from "node:crypto";
import { createServer } from "node:http";
import { chromium } from "playwright";
import AxeBuilder from "@axe-core/playwright";
import { assertAccessible, fitsViewport } from "./accessibility.mjs";
import { observeAnimations } from "./animation-recorder.mjs";
import { checkNonceCsp } from "../../../../scripts/testing/nonce-csp.mjs";

async function nonceFixture(t, { brokenInitial = false } = {}) {
  const server = createServer((request, response) => {
    if (request.url === "/_next/static/chunks/trusted.js") {
      response.writeHead(200, { "Content-Type": "application/javascript" });
      response.end('console.info("Trusted chunk executed")');
      return;
    }
    const nonce = randomBytes(16).toString("base64");
    response.writeHead(200, {
      "Content-Type": "text/html",
      "Content-Security-Policy": `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'`,
    });
    response.end(`<!doctype html><html lang="en"><head><title>Nonce fixture</title></head>
      <body><main><h1>Nonce fixture</h1></main>
      <script${brokenInitial ? "" : ` nonce="${nonce}"`}>
        const chunk = document.createElement("script");
        chunk.src = "/_next/static/chunks/trusted.js";
        document.body.appendChild(chunk);
      </script></body></html>`);
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(() => new Promise((resolve) => server.close(resolve)));
  return `http://127.0.0.1:${server.address().port}`;
}

test("the nonce gate accepts chunks loaded by a trusted nonced bootstrap", async (t) => {
  const url = await nonceFixture(t);
  const browser = await chromium.launch();
  try {
    const context = await browser.newContext();
    const executions = [];
    context.on("page", (page) => {
      page.on("console", (message) => {
        if (message.text() === "Trusted chunk executed") executions.push(message.text());
      });
    });
    await checkNonceCsp(context, url);
    assert.equal(executions.length, 2, "The nonce-less chunk executes on both real navigations");
  } finally {
    await browser.close();
  }
});

test("the nonce gate rejects an initial bootstrap without its response nonce", async (t) => {
  const url = await nonceFixture(t, { brokenInitial: true });
  const browser = await chromium.launch();
  try {
    const context = await browser.newContext();
    await assert.rejects(checkNonceCsp(context, url), /Server-rendered script must use/);
  } finally {
    await browser.close();
  }
});

test("the accessibility gate rejects contrast, missing names and invalid ARIA", async () => {
  const browser = await chromium.launch();
  try {
    const context = await browser.newContext();
    const page = await context.newPage();
    const document = (content) =>
      `<!doctype html><html lang="en"><head><title>Accessibility gate fixture</title></head><body><main><h1>Reader controls</h1>${content}</main></body></html>`;
    await page.setContent(
      document(
        '<button aria-expanded="false">Feed filters</button><p style="color:#111;background:#fff">Readable text</p>',
      ),
    );
    assertAccessible(await new AxeBuilder({ page }).analyze(), "valid fixture");
    for (const [name, content, rule] of [
      ["contrast", '<p style="color:#bbb;background:#fff">Unreadable text</p>', "color-contrast"],
      ["name", '<button><svg aria-hidden="true"></svg></button>', "button-name"],
      ["ARIA", '<button aria-expanded="invalid">Feed filters</button>', "aria-valid-attr-value"],
    ]) {
      await page.setContent(document(content));
      const result = await new AxeBuilder({ page }).analyze();
      assert.ok(
        result.violations.some((violation) => violation.id === rule),
        `${name} regression is detected`,
      );
      assert.throws(() => assertAccessible(result, name), /Accessibility violations/);
    }
  } finally {
    await browser.close();
  }
});

test("motion observations survive navigation and flush browser callbacks", async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    const animations = await observeAnimations(page, async () => {
      for (let index = 0; index < 2; index++) {
        await page.evaluate(() => {
          const target = document.createElement("div");
          target.className = "motion-target";
          document.body.append(target);
          target.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 10 });
        });
        await page.reload();
      }
    });
    assert.deepEqual(animations, ["motion-target", "motion-target"]);
    assert.deepEqual(await observeAnimations(page, () => page.reload()), []);
  } finally {
    await browser.close();
  }
});

test("viewport checks wait for bookmark hydration and retain strict bounds", async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 320, height: 844 } });
    await page.setContent('<main><button class="bookmark-button" hidden>Loading</button></main>');
    await page.evaluate(() => {
      requestAnimationFrame(() => {
        const link = document.createElement("a");
        link.className = "bookmark-button";
        link.textContent = "Save";
        link.style.cssText = "display:block;width:24px;height:24px";
        document.querySelector(".bookmark-button").replaceWith(link);
      });
    });
    await fitsViewport(page);
    await page.locator(".bookmark-button").evaluate((node) => (node.style.width = "23px"));
    await assert.rejects(fitsViewport(page), /Bookmark target remains usable/);
    await page.locator(".bookmark-button").evaluate((node) => (node.style.width = "24px"));
    await page.evaluate(() => (document.body.style.width = "500px"));
    await assert.rejects(fitsViewport(page), /without page-level horizontal scrolling/);
  } finally {
    await browser.close();
  }
});
