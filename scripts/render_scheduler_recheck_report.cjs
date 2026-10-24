const fs = require("node:fs");
const path = require("node:path");
const { parseArgs } = require("node:util");

const expectedBaselineFailure = "clock-only rechecks generated card events or hydration";
const finite = (value) => typeof value === "number" && Number.isFinite(value) && value >= 0;
const object = (value) => value && typeof value === "object" && !Array.isArray(value);
const ordered = (value) =>
  Object.entries(value).sort(([left], [right]) => left.localeCompare(right));
const same = (left, right) => JSON.stringify(ordered(left)) === JSON.stringify(ordered(right));
const escape = (value) =>
  String(value)
    .replace(/[\n\r|`<>]/g, " ")
    .slice(0, 400);
const sumTicks = (report, read) => {
  if (!Array.isArray(report?.ticks) || report.ticks.length === 0) return null;
  const values = report.ticks.map((tick) => (object(tick) ? read(tick) : null));
  return values.every(finite) ? values.reduce((total, value) => total + value, 0) : null;
};

const metrics = [
  ["Workload runtime (s)", (report) => report?.workload_seconds, 3],
  ["Application CPU (s)", (report) => report?.application_cpu_seconds, 3],
  ["Clock-only events", (report) => sumTicks(report, (tick) => tick.cohort_events)],
  [
    "Cohort card hydrations",
    (report) =>
      sumTicks(report, (tick) =>
        object(tick.hydration) ? (tick.hydration.cohort_ids ?? 0) : null,
      ),
  ],
  ...[
    ["SQL calls", "calls"],
    ["Shared blocks hit", "shared_blks_hit"],
    ["Shared blocks read", "shared_blks_read"],
    ["Temporary blocks written", "temp_blks_written"],
    ["WAL records", "wal_records"],
    ["WAL full-page images", "wal_fpi"],
    ["WAL bytes", "wal_bytes"],
  ].map(([label, key]) => [label, (report) => report?.postgres_workload_delta?.[key]]),
];

function reportFailures(report, label, articles) {
  if (!object(report)) return [`${label} report missing`];
  const failures = [];
  const fail = (message) => failures.push(`${label}: ${message}`);
  if (report.complete !== true) fail("run incomplete");
  if (report.checkpoint_before_workload !== true) fail("initial checkpoint unverified");
  if (
    report.hardware?.architecture !== "aarch64" ||
    !/\bon aarch64[-\s]/.test(report.resources?.postgres?.version ?? "")
  )
    fail("native ARM64 unverified");
  if (!/^PostgreSQL 17[.\s]/.test(report.resources?.postgres?.version ?? ""))
    fail("PostgreSQL 17 unverified");
  if (!object(report.dataset) || report.dataset.articles !== Number(articles))
    fail(`dataset is not ${Number(articles).toLocaleString("en-US")} articles`);
  const production = report.production_2026_10_24_0928_utc;
  if (
    !object(production) ||
    Object.keys(production).length === 0 ||
    Object.entries(production).some(
      ([key, value]) =>
        !finite(value) || !finite(report.dataset?.[key]) || report.dataset[key] < value,
    ) ||
    !object(report.below_production_population) ||
    Object.keys(report.below_production_population).length !== 0
  )
    fail("production population floor unverified or unmet");
  const workload = report.workload;
  if (
    !object(workload) ||
    ["scale", "ticks", "batch", "ingest_per_tick", "history_bytes"].some(
      (key) => !finite(workload[key]) || workload[key] === 0,
    )
  )
    fail("workload missing or invalid");
  if (!Array.isArray(report.ticks) || report.ticks.length !== workload?.ticks) {
    fail("measured ticks incomplete");
  } else {
    for (const tick of report.ticks) {
      if (!object(tick)) {
        fail("measured tick evidence missing");
        continue;
      }
      if (
        tick.outcome?.articles_checked !== workload.batch ||
        tick.outcome?.articles_published !== 0 ||
        tick.outcome?.articles_rejected !== 0 ||
        tick.ingested !== workload.ingest_per_tick ||
        tick.ingested_projected !== workload.ingest_per_tick ||
        tick.ingestion_overlapped_scheduler !== true ||
        tick.active_analysis_unchanged !== true ||
        tick.cohort_clock_visible !== true ||
        tick.cohort_retry_transition_correct !== true
      )
        fail("bounded work, concurrent ingestion or recovery checks failed");
      if (
        !object(tick.publisher_deleted_rows) ||
        Object.values(tick.publisher_deleted_rows).some((value) => value !== 0)
      )
        fail("retention cleanup entered the workload or was unmeasured");
      if (
        label === "After" &&
        (tick.cohort_events !== 0 ||
          !object(tick.hydration) ||
          (tick.hydration.cohort_ids ?? 0) !== 0)
      )
        fail("clock-only events or cohort hydration remain");
    }
  }
  if (
    report.plans?.scheduler_due_candidates?.plan?.[0]?.Plan?.["Actual Rows"] !== workload?.batch ||
    !finite(workload?.batch)
  )
    fail("due-candidate plan did not return the configured batch");
  if (
    !Array.isArray(report.bounded_failures) ||
    report.bounded_failures.some(
      (failure) =>
        label !== "Before" ||
        !Array.isArray(failure?.reasons) ||
        failure.reasons.length === 0 ||
        failure.reasons.some((reason) => reason !== expectedBaselineFailure),
    )
  )
    fail("bounded-work assertions failed or are missing");
  if (metrics.some(([, read]) => !finite(read(report)))) fail("required measurements missing");
  return [...new Set(failures)];
}

function compare(pair, articles) {
  const failures = [
    ...reportFailures(pair?.baseline, "Before", articles),
    ...reportFailures(pair?.candidate, "After", articles),
  ];
  if (pair?.error) failures.push(escape(pair.error));
  for (const key of ["dataset", "workload"]) {
    if (
      object(pair?.baseline?.[key]) &&
      object(pair?.candidate?.[key]) &&
      !same(pair.baseline[key], pair.candidate[key])
    )
      failures.push(`Before/after ${key} differ`);
  }
  return failures;
}

const number = (value, decimals = 0) =>
  finite(value)
    ? value.toLocaleString("en-US", {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
      })
    : "unavailable";
const signed = (value, decimals = 0) =>
  `${value > 0 ? "+" : value < 0 ? "-" : ""}${number(Math.abs(value), decimals)}`;
const improvement = (before, after) =>
  before === 0
    ? after === 0
      ? "0.0%"
      : "- (new cost)"
    : `${signed(((before - after) / before) * 100, 1)}%`;
const sha = (value) => (/^[a-f\d]{40}$/i.test(value ?? "") ? `\`${value}\`` : "unavailable");

function renderReport({ reports = {}, runUrl, headSha, baseSha, conclusion = "success" } = {}) {
  const lines = [];
  const scales = Object.keys(reports).sort((left, right) => Number(left) - Number(right));
  if (!scales.length) lines.push("**Unavailable:** no dataset reports supplied.");
  for (const articles of scales) {
    const pair = reports[articles];
    const failures = compare(pair, articles);
    if (conclusion !== "success") failures.push(`Workflow conclusion: ${escape(conclusion)}`);
    lines.push(`### ${Number(articles).toLocaleString("en-US")} articles`, "");
    if (failures.length) {
      lines.push(
        "**Unavailable comparison.** Measurements below do not establish a valid speedup.",
        "",
      );
      lines.push(...failures.map((failure) => `- ${escape(failure)}`), "");
    } else {
      lines.push(
        "**Valid controlled comparison:** native ARM64, PostgreSQL 17, matched dataset and workload.",
        "",
      );
    }
    lines.push(
      "| Metric | Before | After | Delta | Improvement (+/-) |",
      "| --- | ---: | ---: | ---: | ---: |",
    );
    for (const [label, read, decimals] of metrics) {
      const before = read(pair?.baseline);
      const after = read(pair?.candidate);
      const valid = failures.length === 0 && finite(before) && finite(after);
      lines.push(
        `| ${label} | ${number(before, decimals)} | ${number(after, decimals)} | ${valid ? signed(after - before, decimals) : "unavailable"} | ${valid ? improvement(before, after) : "unavailable"} |`,
      );
    }
    lines.push("");
    if (
      Array.isArray(pair?.baseline?.bounded_failures) &&
      pair.baseline.bounded_failures.some(
        (failure) =>
          Array.isArray(failure?.reasons) && failure.reasons.includes(expectedBaselineFailure),
      )
    )
      lines.push("Before includes the expected clock-only event/hydration amplification.", "");
  }
  lines.push(
    "Positive improvement means reduced cost; negative means increased cost. Delta = after - before.",
    "",
    `Base ${sha(baseSha)} → candidate ${sha(headSha)}.`,
    /^https:\/\/[^\s)]+$/.test(runUrl ?? "")
      ? `[Workflow run](${runUrl}).`
      : "Workflow run unavailable.",
  );
  return `${lines.join("\n").trim()}\n`;
}

function main() {
  const { values } = parseArgs({
    options: Object.fromEntries(
      ["articles", "directory", "output", "run-url", "head-sha", "base-sha", "conclusion"].map(
        (key) => [key, { type: "string" }],
      ),
    ),
  });
  if (!["100000", "150000"].includes(values.articles))
    throw new Error("--articles must be 100000 or 150000");
  const directory = workspacePath(values.directory ?? "reports/scheduler-rechecks");
  const pair = {};
  const errors = [];
  for (const label of ["baseline", "candidate"]) {
    try {
      const filename = workspacePath(path.join(directory, `${label}.json`));
      pair[label] = JSON.parse(fs.readFileSync(filename, "utf8"));
    } catch (error) {
      errors.push(`${label} report unavailable (${error.code ?? "invalid JSON"})`);
    }
  }
  if (errors.length) pair.error = errors.join("; ");
  const conclusion = values.conclusion ?? "success";
  const output = workspacePath(values.output ?? path.join(directory, "comment.md"));
  fs.mkdirSync(path.dirname(output), { recursive: true });
  const descriptor = fs.openSync(
    output,
    fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_TRUNC | fs.constants.O_NOFOLLOW,
    0o600,
  );
  try {
    fs.writeFileSync(
      descriptor,
      renderReport({
        reports: { [values.articles]: pair },
        runUrl: values["run-url"],
        headSha: values["head-sha"],
        baseSha: values["base-sha"],
        conclusion,
      }),
    );
  } finally {
    fs.closeSync(descriptor);
  }
  if (compare(pair, values.articles).length || conclusion !== "success") process.exitCode = 1;
}

function workspacePath(filename) {
  const root = fs.realpathSync(process.cwd());
  const target = path.resolve(root, filename);
  const relative = path.relative(root, target);
  if (relative === ".." || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative))
    throw new Error("Benchmark report paths must remain inside the working directory");
  let current = root;
  for (const part of relative.split(path.sep).filter(Boolean)) {
    current = path.join(current, part);
    try {
      if (fs.lstatSync(current).isSymbolicLink())
        throw new Error("Benchmark report paths must not follow symbolic links");
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
      break;
    }
  }
  return target;
}

module.exports = { renderReport };
if (require.main === module) main();
