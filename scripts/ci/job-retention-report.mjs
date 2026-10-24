import { appendFileSync, lstatSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

const sizes = [250000, 100000];
const phases = [
  "backlog",
  "convergence_prune1000_scan1000",
  "latest_only",
  "ordinary_ticks",
  "ordinary_ticks_no_writer",
  "deployed_cadence",
];

function number(value) {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0)
    throw new Error("Missing measurement");
  return value;
}

const value = (item, path) => number(path.split(".").reduce((item, key) => item?.[key], item));
const decimal = (item) => number(item).toFixed(3);
const integer = (item) => number(item).toLocaleString("en-US");
const improvement = (before, after) =>
  before === 0 && after !== 0
    ? "n/a"
    : `${after <= before ? "+" : ""}${(before === 0 ? 0 : ((before - after) / before) * 100).toFixed(2)}%`;

function table(rows) {
  return [
    "| Metric | Before | After | Improvement (+/-) |",
    "| --- | ---: | ---: | ---: |",
    ...rows.map(([name, before, after, unit = "", scale = 1]) => {
      const format = unit === "count" ? integer : decimal;
      const suffix = unit && unit !== "count" ? ` ${unit}` : "";
      return `| ${name} | ${format(before / scale)}${suffix} | ${format(after / scale)}${suffix} | ${improvement(before, after)} |`;
    }),
    "",
  ];
}

function planStats(plans) {
  if (!Array.isArray(plans) || !plans.length) throw new Error("Missing query plans");
  return {
    rows: Math.max(...plans.map((item) => value(item, "examined_job_rows"))),
    buffers: Math.max(
      ...plans.map(
        (item) =>
          number(item.plan.Plan["Shared Hit Blocks"] ?? 0) +
          number(item.plan.Plan["Shared Read Blocks"] ?? 0),
      ),
    ),
    temporary: Math.max(
      ...plans.map(
        (item) =>
          number(item.plan.Plan["Temp Read Blocks"] ?? 0) +
          number(item.plan.Plan["Temp Written Blocks"] ?? 0),
      ),
    ),
  };
}

function dataset(data, articles) {
  if (
    data.articles !== articles ||
    data.application_architecture !== "aarch64" ||
    !/^PostgreSQL 17\./.test(data.postgres_version)
  )
    throw new Error("Unexpected benchmark platform or size");
  for (const prefix of ["baseline", "current"])
    for (const phase of phases) value(data.phases?.[`${prefix}_${phase}`], "retention.calls");
  const pair = (phase) => [data.phases[`baseline_${phase}`], data.phases[`current_${phase}`]];
  const row = (name, phase, path, unit = "ms", scale = 1) => [
    name,
    ...pair(phase).map((item) => value(item, path)),
    unit,
    scale,
  ];
  const latest = pair("latest_only").map((item) => planStats(item.plans));
  const deep = planStats(pair("latest_only")[1].deep_cursor_plans);
  const cadence = pair("deployed_cadence");
  if (
    cadence.some(
      (item) =>
        value(item, "retention.calls") !== 5 || value(item, "real_tick_interval_seconds") !== 15,
    ) ||
    pair("backlog").some((item) => value(item, "retention.calls") !== 50)
  )
    throw new Error("Unexpected measurement cadence");
  const costRows = (phase) => [
    row("Application CPU", phase, "application_cpu_seconds_including_ingestion", "s"),
    row("Total WAL", phase, "wal_bytes_including_ingestion", "MB", 1000000),
  ];
  return [
    `**${integer(articles)} articles / ${integer(value(data, "jobs_per_kind") * 2)} analysis jobs**`,
    "",
    "**Idle and protected history**",
    "",
    ...table([
      row("Idle retention median", "ordinary_ticks_no_writer", "retention.median_ms"),
      row("Idle retention p95", "ordinary_ticks_no_writer", "retention.p95_ms"),
      row("Latest-only due-page p95", "latest_only", "retention.p95_ms"),
      ["Latest-only maximum examined job rows/query", ...latest.map((item) => item.rows), "count"],
      ["Latest-only maximum shared buffers/query", ...latest.map((item) => item.buffers), "count"],
    ]),
    `At the 80% latest-only cursor: at most ${integer(deep.rows)} examined job rows/query, ${integer(deep.buffers)} shared buffers/query, ${integer(deep.temporary)} temporary blocks.`,
    "",
    "**Deployed cadence — five real 15-second scheduler ticks / 75 seconds**",
    "",
    ...table([
      row("Ingestion p95", "deployed_cadence", "ingestion.p95_ms"),
      row("Ingestion p99", "deployed_cadence", "ingestion.p99_ms"),
      row("Worst ingestion transaction", "deployed_cadence", "ingestion.max_ms"),
      ...costRows("deployed_cadence"),
    ]),
    `Ingestion transactions: **${cadence.map((item) => integer(value(item, "ingestion.calls"))).join(" → ")}**. Payloads pruned: **${cadence.map((item) => integer(value(item, "pruned"))).join(" → ")}**. Lower phase WAL includes the changed maintenance frequency; these are not equal-prune costs.`,
    "",
    "**Compressed stress — 50 consecutive pages**",
    "",
    ...table([
      row("Retention p95", "backlog", "retention.p95_ms"),
      row("Ingestion p95", "backlog", "ingestion.p95_ms"),
      ...costRows("backlog"),
    ]),
    `Payloads pruned: **${pair("backlog")
      .map((item) => integer(value(item, "pruned")))
      .join(
        " → ",
      )}**. This deliberately compresses scheduler spacing; use the deployed-cadence table for the sustained writer comparison.`,
    "",
    `Fixture: ${decimal(value(data, "payload_bytes") / 1024)} KiB full-history inputs; ${decimal(value(data, "cadence_payload_bytes") / 1024)} KiB cadence input/catalog payloads on ${integer(value(data, "cadence_subjects_per_kind"))} subjects per history. The entire history does not use the larger payload.`,
    "",
  ].join("\n");
}

export function readRetentionReports(directory) {
  return sizes.map((articles) => {
    try {
      const path = join(directory, `${articles}.json`);
      const stat = lstatSync(path);
      if (!stat.isFile() || stat.size > 20000000) throw new Error("Invalid report file");
      return { articles, data: JSON.parse(readFileSync(path, "utf8")) };
    } catch {
      return { articles, error: "Report missing or invalid" };
    }
  });
}

export function retentionSummary(reports) {
  const sections = [];
  const unavailable = [];
  for (const articles of sizes) {
    try {
      const report = reports.find((item) => item.articles === articles);
      if (!report?.data) throw new Error("Report unavailable");
      const body = dataset(report.data, articles);
      sections.push(
        articles === 100000
          ? `<details>\n<summary>100k results</summary>\n\n${body}\n</details>\n`
          : body,
      );
    } catch {
      unavailable.push(`${integer(articles)}-article report missing, invalid, or incomplete.`);
    }
  }
  return {
    complete: unavailable.length === 0,
    body: [
      "**Positive improvement = lower latency/cost; negative = regression.** Native ARM64 / PostgreSQL 17. Decimal MB; benchmark observations are not production latency guarantees.",
      "",
      ...unavailable.map((message) => `**Results unavailable: ${message}**\n`),
      ...sections,
      "CPU is Python application process time including ingestion and the wait sampler, not database-server CPU. WAL includes ingestion. Each phase starts after an explicit checkpoint; automatic checkpoints remain enabled.",
    ].join("\n"),
  };
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const [, , directory, output] = process.argv;
  const summary = retentionSummary(readRetentionReports(directory));
  mkdirSync(dirname(output), { recursive: true });
  writeFileSync(output, JSON.stringify(summary));
  if (process.env.GITHUB_STEP_SUMMARY)
    appendFileSync(process.env.GITHUB_STEP_SUMMARY, `${summary.body}\n`);
}
