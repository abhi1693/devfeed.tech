import assert from "node:assert/strict";
import { readFile, writeFile } from "node:fs/promises";
const root = new URL("../../reports/document-latency/", import.meta.url);
const [before, after] = await Promise.all(
  ["before", "after"].map(async (phase) =>
    JSON.parse(await readFile(new URL(`${phase}/measurements.json`, root), "utf8")),
  ),
);
const median = (values) => [...values].sort((a, b) => a - b)[Math.floor(values.length / 2)];
const spread = (values) => Math.max(...values) - Math.min(...values);
const keys = [...new Set(before.rows.map(({ route, identity }) => `${identity} ${route}`))];
const results = [];
for (const key of keys) {
  const groups = [before, after].map(({ rows }) =>
    rows.filter(({ route, identity }) => `${identity} ${route}` === key),
  );
  assert.deepEqual(
    groups[0].map(({ upstreamDelayMs }) => upstreamDelayMs),
    groups[1].map(({ upstreamDelayMs }) => upstreamDelayMs),
    "Use the same upstream delay sequence",
  );
  results.push(
    `| ${key} | ${groups.map((rows) => rows[0].ttfbMs.toFixed(1)).join(" → ")} | ${groups.map((rows) => median(rows.slice(1).map(({ ttfbMs }) => ttfbMs)).toFixed(1)).join(" → ")} | ${groups.map((rows) => spread(rows.slice(1).map(({ ttfbMs }) => ttfbMs)).toFixed(1)).join(" → ")} | ${groups.map((rows) => median(rows.slice(1).map(({ completeMs }) => completeMs)).toFixed(1)).join(" → ")} | ${groups.map((rows) => rows[1].requests.length).join(" → ")} |`,
  );
}
const home = (data) =>
  data.rows
    .filter(({ route, identity, coldRoute }) => route === "/" && identity === "guest" && !coldRoute)
    .map(({ ttfbMs }) => ttfbMs);
assert.ok(median(home(after)) < median(home(before)), "Guest homepage sends its shell sooner");
assert.ok(spread(home(after)) < spread(home(before)), "Guest homepage latency spread decreases");
const topic = (data) => data.rows.filter(({ route }) => route === "/topics/typescript");
for (const [data, expected] of [
  [before, 2],
  [after, 1],
])
  for (const row of topic(data))
    assert.equal(row.requests.filter(({ path }) => path === "/v1/feed").length, expected);
const report = `# Document latency comparison\n\nMilliseconds, before → after. Each route/identity has a fresh Next process, one first-route request after the readiness probe, then seven warm requests. The anonymous fixture has 24 articles/60 catalog entries. Upstream delays are the same fixed sequence (240, 80, 140, 60, 120, 90, 180, 100 ms); this separates response blocking from backend variability. These are local production builds, not field measurements or database/cache cold-start results.\n\n| Document | First-route TTFB | Warm median TTFB | Warm TTFB range width | Warm median full response | API requests/document |\n| --- | ---: | ---: | ---: | ---: | ---: |\n${results.join("\n")}\n\nFull content still waits for fresh upstream data. The initial shell is what arrives sooner. Topic metadata still waits for its required visibility/feed checks; sharing its feed response reduces requests without establishing a topic-latency improvement. Raw OTLP spans record metadata, rendering and individual upstream durations separately.\n`;
let lighthouseReport = "\nLighthouse reports were not supplied for both revisions.\n";
const audits = await Promise.all(
  ["before", "after"].map(async (phase) => {
    try {
      return JSON.parse(await readFile(new URL(`${phase}/lighthouse.json`, root), "utf8"));
    } catch (error) {
      if (error.code === "ENOENT") return undefined;
      throw error;
    }
  }),
);
if (audits.every(Boolean)) {
  assert.deepEqual(
    audits[0].map(({ route, profile, run, lighthouse, browser }) => ({
      route,
      profile,
      run,
      lighthouse,
      browser,
    })),
    audits[1].map(({ route, profile, run, lighthouse, browser }) => ({
      route,
      profile,
      run,
      lighthouse,
      browser,
    })),
    "Use the same Lighthouse/browser profiles",
  );
  for (const audit of audits[1])
    assert.ok(
      audit.responseMs <= 600 && audit.score === 1,
      "Candidate passes the 600 ms document response audit",
    );
  const lines = [];
  for (const route of ["/", "/latest", "/sources"])
    for (const profile of ["mobile", "desktop"]) {
      const ranges = audits.map((rows) =>
        rows
          .filter((row) => row.route === route && row.profile === profile)
          .map(({ responseMs }) => responseMs.toFixed(1))
          .join(", "),
      );
      lines.push(`| ${route} | ${profile} | ${ranges.join(" | ")} |`);
    }
  lighthouseReport = `\nLighthouse 12.6.1 server-response-time (ms), two fresh browsers per profile; 80 ms fixture upstream. All candidate samples pass ≤600 ms.\n\n| Route | Profile | Before samples | After samples |\n| --- | --- | ---: | ---: |\n${lines.join("\n")}\n`;
}
await writeFile(new URL("comparison.md", root), report + lighthouseReport);
console.log(report + lighthouseReport);
