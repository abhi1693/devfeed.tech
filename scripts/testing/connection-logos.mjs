import assert from "node:assert/strict";
import { join } from "node:path";
import sharp from "sharp";

export const logoSource = "https://catalog.example/brand/logo.png";
export const managedLogo = "https://assets.partner.example/connection-logos/url-64.png";
export const uploadedLogo = "https://assets.partner.example/connection-logos/upload-64.png";
export const logoFile = {
  name: "launch-platform.png",
  mimeType: "image/png",
  buffer: await sharp(
    Buffer.from(
      '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"><rect width="64" height="64" rx="12" fill="#6d28d9"/><path d="M17 17h9v23h21v8H17z" fill="white"/></svg>',
    ),
  )
    .png()
    .toBuffer(),
};

export function connectionLogo(url = null, uploaded = false) {
  const managed = uploaded
    ? uploadedLogo
    : url?.startsWith("https://assets.partner.example/connection-logos/")
      ? url
      : url
        ? managedLogo
        : null;
  return {
    logo_url: managed,
    logo_variants: managed
      ? [32, 64].map((width) => ({ url: managed.replace("64.png", `${width}.png`), width }))
      : [],
  };
}

export async function readLogoUpload(request, csrf, origin) {
  assert.equal(request.headers["x-csrf-token"], csrf);
  if (origin) assert.equal(request.headers.origin, origin);
  assert.match(request.headers["content-type"], /^multipart\/form-data; boundary=/);
  const chunks = [];
  for await (const chunk of request) chunks.push(chunk);
  const form = await new Response(Buffer.concat(chunks), {
    headers: { "content-type": request.headers["content-type"] },
  }).formData();
  assert.deepEqual([...form.keys()].sort(), ["expected_revision", "file"]);
  const file = form.get("file");
  assert.equal(file.name, logoFile.name);
  assert.equal(file.type, logoFile.mimeType);
  assert.deepEqual(Buffer.from(await file.arrayBuffer()), logoFile.buffer);
  return { expected_revision: Number(form.get("expected_revision")), file: file.name };
}

export async function readLogoUrl(request, csrf, origin) {
  assert.equal(request.headers["x-csrf-token"], csrf);
  if (origin) assert.equal(request.headers.origin, origin);
  const chunks = [];
  for await (const chunk of request) chunks.push(chunk);
  const input = JSON.parse(Buffer.concat(chunks).toString());
  assert.deepEqual(Object.keys(input).sort(), ["expected_revision", "logo_url"]);
  return input;
}

export async function installConnectionLogoMedia(context) {
  await context.route("https://assets.partner.example/connection-logos/**", (route) =>
    route.fulfill({
      contentType: "image/svg+xml",
      body: '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"><rect width="64" height="64" rx="12" fill="#6d28d9"/><path d="M17 17h9v23h21v8H17z" fill="white"/></svg>',
    }),
  );
}

export async function assertManagedConnectionLogo(scope, url) {
  const image = scope.locator(`img[src="${url}"]`).first();
  await image.waitFor();
  await image.evaluate((node) => node.decode());
  assert.equal(await image.getAttribute("alt"), "");
  assert.equal(await image.getAttribute("referrerpolicy"), "no-referrer");
  assert.equal(
    await image.getAttribute("srcset"),
    connectionLogo(logoSource, url === uploadedLogo)
      .logo_variants.map((variant) => `${variant.url} ${variant.width}w`)
      .join(", "),
  );
  assert.equal(
    await scope.locator(`img[src="${logoSource}"]`).count(),
    0,
    "Connection names only display managed presentation URLs",
  );
  return image;
}

export async function selectConnectionLogo(page, { immediate = false } = {}) {
  const field = page.getByRole("group", { name: "Platform logo", exact: true });
  await field.getByRole("button", { name: "Upload logo", exact: true }).waitFor();
  if (immediate)
    await field
      .getByRole("textbox", { name: "Logo URL", exact: true })
      .fill("https://catalog.example/brand/replaced-by-upload.png");
  const chooserReady = page.waitForEvent("filechooser", { timeout: 5000 });
  await field.getByRole("button", { name: "Upload logo", exact: true }).click();
  const chooser = await chooserReady;
  await chooser.setFiles(logoFile);
  const preview = field
    .getByRole("img", { name: "Platform logo preview", exact: true })
    .locator('img[src^="blob:"]');
  await preview.waitFor();
  await preview.evaluate((image) => image.decode());
  assert.equal(
    await preview.evaluate((image) => image.naturalWidth),
    64,
    "The selected file is previewed locally before it is persisted",
  );
  assert.equal(
    await field.getByRole("textbox", { name: "Logo URL", exact: true }).inputValue(),
    "",
  );
  if (!immediate) {
    await field
      .getByText("Your logo will be saved when you create this connection.", { exact: true })
      .waitFor();
    await field.getByText(logoFile.name, { exact: true }).waitFor();
  }
}

export async function removeConnectionLogoFromEditedUrl(page, counts) {
  const field = page.getByRole("group", { name: "Platform logo", exact: true });
  await field
    .getByRole("textbox", { name: "Logo URL", exact: true })
    .fill("https://catalog.example/brand/discarded-by-remove.png");
  const before = counts();
  await field.getByRole("button", { name: "Remove logo", exact: true }).click();
  await field.getByRole("status").filter({ hasText: "Logo removed." }).waitFor();
  assert.deepEqual(
    counts(),
    { ...before, url: before.url + 1 },
    "The first Remove click removes the logo without importing the focused URL or saving settings",
  );
  assert.equal(
    await field.getByRole("textbox", { name: "Logo URL", exact: true }).inputValue(),
    "",
  );
}

export async function saveConnectionFromEditedLogoUrl(page, destination, counts) {
  const field = page.getByRole("group", { name: "Platform logo", exact: true });
  await field.getByRole("textbox", { name: "Logo URL", exact: true }).fill(logoSource);
  const before = counts();
  await page.getByRole("button", { name: "Save changes", exact: true }).click();
  await page.waitForURL(destination);
  assert.deepEqual(
    counts(),
    { ...before, settings: before.settings + 1 },
    "The first Save click consumes the URL draft in one settings save without an independent blur import",
  );
}

export async function assertInvalidConnectionLogo(page, writeCount) {
  const field = page.getByRole("group", { name: "Platform logo", exact: true });
  const before = writeCount();
  await field.getByLabel("Logo image file", { exact: true }).setInputFiles({
    name: "unsupported.svg",
    mimeType: "image/svg+xml",
    buffer: Buffer.from("<svg/>"),
  });
  await field.getByRole("alert").filter({ hasText: "Choose a JPEG, PNG or WebP image." }).waitFor();
  assert.equal(
    await page.getByRole("button", { name: "Save changes", exact: true }).isDisabled(),
    true,
    "Invalid image selection blocks saving unrelated settings",
  );
  await page
    .getByRole("form", { name: "Import connection settings", exact: true })
    .evaluate((form) => form.requestSubmit());
  await page.evaluate(
    () => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))),
  );
  assert.equal(
    writeCount(),
    before,
    "An invalid file cannot submit settings or upload a previous draft",
  );
  await field.getByRole("button", { name: "Remove logo", exact: true }).click();
  await field.getByRole("alert").waitFor({ state: "hidden" });
  await field.getByRole("status").filter({ hasText: "Logo removed." }).waitFor();
  assert.equal(
    await page.getByRole("button", { name: "Save changes", exact: true }).isDisabled(),
    false,
    "Removing the invalid selection restores saving",
  );
}

export async function assertConnectionLogoEditor(page, output, prefix) {
  const originalViewport = page.viewportSize();
  const originalTheme = await page.evaluate(() => ({
    theme: document.documentElement.getAttribute("data-theme"),
    dark: document.documentElement.classList.contains("dark"),
    colorScheme: document.documentElement.style.colorScheme,
  }));
  try {
    for (const theme of ["light", "dark"]) {
      await page.evaluate((mode) => {
        document.documentElement.dataset.theme = mode;
        document.documentElement.classList.toggle("dark", mode === "dark");
        document.documentElement.style.colorScheme = mode;
      }, theme);
      for (const width of [1440, 390]) {
        await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
        const field = page.getByRole("group", { name: "Platform logo", exact: true });
        const image = await assertManagedConnectionLogo(field, managedLogo);
        await field.scrollIntoViewIfNeeded();
        const box = await image.boundingBox();
        assert.equal(box.width, 56);
        assert.equal(box.height, 56);
        const layout = await field.evaluate((node) => ({
          field: node.getBoundingClientRect().toJSON(),
          input: node.querySelector('input[type="url"]').getBoundingClientRect().toJSON(),
          overflow: document.documentElement.scrollWidth > innerWidth + 1,
          color: getComputedStyle(node).color,
        }));
        assert.ok(layout.input.x >= layout.field.x && layout.input.right <= layout.field.right + 1);
        assert.equal(layout.overflow, false, "Logo controls fit the editor on narrow screens");
        assert.notEqual(layout.color, "rgba(0, 0, 0, 0)");
        await field.screenshot({
          path: join(output, `${prefix}-logo-editor-${theme}-${width}.png`),
        });
        await page.evaluate(() => window.scrollTo(0, 0));
        await page.screenshot({
          path: join(output, `${prefix}-logo-form-${theme}-${width}.png`),
          fullPage: true,
        });
        await page.screenshot({
          path: join(output, `${prefix}-logo-form-top-${theme}-${width}.png`),
        });
      }
    }
  } finally {
    await page.evaluate((state) => {
      const root = document.documentElement;
      if (state.theme === null) root.removeAttribute("data-theme");
      else root.setAttribute("data-theme", state.theme);
      root.classList.toggle("dark", state.dark);
      root.style.colorScheme = state.colorScheme;
    }, originalTheme);
    await page.setViewportSize(originalViewport);
  }
}
