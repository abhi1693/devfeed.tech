import assert from "node:assert/strict";
import { readFile, writeFile } from "node:fs/promises";

const root = new URL("../../reports/reader-dom/", import.meta.url);
const [before, after] = await Promise.all(
  ["before", "after"].map(async (phase) =>
    JSON.parse(await readFile(new URL(`${phase}/measurements.json`, root), "utf8")),
  ),
);
assert.equal(before.browser, after.browser, "Use the same browser on both revisions");
assert.equal(before.runs, after.runs);
assert.deepEqual(
  before.results.map(({ profile, path }) => [profile, path]),
  after.results.map(({ profile, path }) => [profile, path]),
);
const counts = [];
const timings = [];
for (let index = 0; index < before.results.length; index++) {
  const a = before.results[index],
    b = after.results[index];
  assert.equal(a.census.cards, b.census.cards);
  assert.deepEqual(
    a.census.content,
    b.census.content,
    `${a.profile} ${a.path}: preserve every card and link`,
  );
  assert.ok(b.census.elements < a.census.elements, "Reduce actual DOM elements");
  assert.ok(b.layoutNodes < a.layoutNodes, "Reduce nodes in the current layout snapshot");
  counts.push(
    `| ${a.profile} | ${a.path} | ${a.census.cards} | ${a.census.elements} → ${b.census.elements} | ${a.layoutNodes} → ${b.layoutNodes} |`,
  );
  timings.push(
    `| ${a.profile} | ${a.path} | ${a.interactionLayoutMs.toFixed(2)} → ${b.interactionLayoutMs.toFixed(2)} | ${a.interactionStyleMs.toFixed(2)} → ${b.interactionStyleMs.toFixed(2)} |`,
  );
}
const report = `# Reader DOM comparison\n\nChromium ${before.browser}; ${before.runs} fresh contexts per route and profile. All card text and native link destinations match.\n\n| Profile | Route | Cards retained | Elements before → after | Layout snapshot nodes before → after |\n| --- | --- | ---: | ---: | ---: |\n${counts.join("\n")}\n\nMedians in milliseconds for six alternating resize/theme cycles. Timings are diagnostic, with no pass/fail speed threshold. They are sensitive to host load; smaller DOM and layout snapshots do not establish an LCP or rendering-speed improvement.\n\n| Profile | Route | Layout ms before → after | Style ms before → after |\n| --- | --- | ---: | ---: |\n${timings.join("\n")}\n`;
await writeFile(new URL("comparison.md", root), report);
console.log(report);
