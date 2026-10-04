import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";

export async function checkSourceFollow(page, url, sources, output) {
  const reads = [];
  const record = (request) => {
    if (
      new URL(request.url()).pathname === "/api/v1/user/preferences/sources" &&
      request.method() === "GET"
    )
      reads.push(request.url());
  };
  page.on("request", record);
  try {
    await page.goto(url);
    const cards = sources.map((source) =>
      page
        .locator(".source-card")
        .filter({ has: page.getByRole("heading", { name: source.name }) }),
    );
    const first = cards[0].getByRole("button", { name: "Follow", exact: true });
    await first.click();
    await cards[0].getByRole("alert").waitFor();
    assert.equal(await first.getAttribute("aria-pressed"), "false");
    await first.click();
    await cards[0].getByRole("button", { name: "Following", exact: true }).waitFor();
    await cards[1].getByRole("button", { name: "Follow", exact: true }).click();
    await cards[1].getByRole("button", { name: "Following", exact: true }).waitFor();
    const initialReads = reads.length;
    assert.ok(initialReads <= 1, "Source controls share one preference read");
    await cards[0].getByRole("button", { name: "Following", exact: true }).click();
    await cards[0].getByRole("button", { name: "Follow", exact: true }).waitFor();
    assert.ok(await cards[1].getByRole("button", { name: "Following", exact: true }).isVisible());
    assert.equal(reads.length, initialReads, "Follow changes update the cache without extra reads");
    await page.reload();
    await cards[1].getByRole("button", { name: "Following", exact: true }).waitFor();
    await cards[0].getByRole("button", { name: "Follow", exact: true }).waitFor();
    await mkdir(output, { recursive: true });
    await page.screenshot({ path: `${output}/source-follow.png` });
  } finally {
    page.off("request", record);
  }
}
