import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import fs, {
  copyFileSync,
  linkSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { mock, test } from "node:test";
import { fileURLToPath } from "node:url";
import {
  readBoundedReport,
  readRetentionReports,
  retentionReportMain,
  retentionSummary,
  runRetentionReport,
} from "./job-retention-report.mjs";

function measurement(articles) {
  const phase = {
    retention: { calls: 5, median_ms: 100, p95_ms: 200 },
    ingestion: { calls: 1498, p95_ms: 5, p99_ms: 10, max_ms: 40 },
    pruned: 1000,
    real_tick_interval_seconds: 15,
    application_cpu_seconds_including_ingestion: 2,
    wal_bytes_including_ingestion: 1000000,
    plans: [{ examined_job_rows: 1000, plan: { Plan: { "Shared Hit Blocks": 2000 } } }],
    deep_cursor_plans: [
      { examined_job_rows: 1000, plan: { Plan: { "Shared Read Blocks": 3000 } } },
    ],
  };
  const phases = {};
  for (const prefix of ["baseline", "current"])
    for (const name of [
      "backlog",
      "convergence_prune1000_scan1000",
      "latest_only",
      "ordinary_ticks",
      "ordinary_ticks_no_writer",
      "deployed_cadence",
    ])
      phases[`${prefix}_${name}`] = {
        ...structuredClone(phase),
        retention: { ...phase.retention, calls: name === "backlog" ? 50 : 5 },
      };
  phases.current_ordinary_ticks_no_writer.retention.median_ms = 2;
  phases.current_backlog.application_cpu_seconds_including_ingestion = 4;
  phases.current_deployed_cadence.pruned = 600;
  phases.current_deployed_cadence.ingestion.p95_ms = 5.1;
  return {
    articles,
    jobs_per_kind: articles * 1.7,
    application_architecture: "aarch64",
    postgres_version: "PostgreSQL 17.11 on aarch64",
    payload_bytes: 4096,
    cadence_payload_bytes: 65536,
    cadence_subjects_per_kind: 1000,
    phases,
  };
}

const reports = () =>
  [100000, 250000].map((articles) => ({ articles, data: measurement(articles) }));

function seedReports(root) {
  const directory = join(root, "reports", "retention");
  mkdirSync(directory, { recursive: true });
  for (const item of reports())
    writeFileSync(join(directory, `${item.articles}.json`), JSON.stringify(item.data));
  return directory;
}

function runReport(root) {
  const previous = process.env.GITHUB_STEP_SUMMARY;
  try {
    delete process.env.GITHUB_STEP_SUMMARY;
    return runRetentionReport(root);
  } finally {
    if (previous === undefined) delete process.env.GITHUB_STEP_SUMMARY;
    else process.env.GITHUB_STEP_SUMMARY = previous;
  }
}

test("formatter leads with 250k and keeps signed regressions and unequal cadence work", () => {
  const summary = retentionSummary(reports());
  assert.equal(summary.complete, true);
  assert.ok(summary.body.indexOf("250,000 articles") < summary.body.indexOf("100,000 articles"));
  assert.match(summary.body, /Before \| After \| Improvement \(\+\/-\)/);
  assert.match(summary.body, /100\.000 ms \| 2\.000 ms \| \+98\.00%/);
  assert.match(summary.body, /5\.000 ms \| 5\.100 ms \| -2\.00%/);
  assert.match(summary.body, /2\.000 s \| 4\.000 s \| -100\.00%/);
  assert.match(summary.body, /1,000 → 600/);
  assert.match(summary.body, /not equal-prune costs/);
  assert.match(summary.body, /4\.000 KiB full-history.*64\.000 KiB cadence/);
  assert.match(summary.body, /including ingestion and the wait sampler/);
  assert.match(summary.body, /3,000 shared buffers\/query, 0 temporary blocks/);
});

test("formatter reports incomplete measurements without invented values or leaked input", () => {
  for (const change of [
    (items) => items.pop(),
    (items) => delete items[0].data.phases.current_backlog,
    (items) => (items[0].data.phases.current_backlog.ingestion.p95_ms = NaN),
    (items) => (items[0].data.phases.current_latest_only.plans = []),
    (items) =>
      (items[0].data.phases.current_latest_only.deep_cursor_plans[0].examined_job_rows = -1),
    (items) => (items[0].data.articles = 1),
    (items) => (items[0].data.application_architecture = "x86_64"),
    (items) => (items[0].data.postgres_version = "Sensitive internal detail"),
    (items) => (items[0].data.phases.current_deployed_cadence.real_tick_interval_seconds = 0.05),
    (items) => (items[0].data.phases.current_deployed_cadence.retention.calls = 4),
    (items) => (items[0].data.phases.current_backlog.retention.calls = 1),
  ]) {
    const items = reports();
    change(items);
    const summary = retentionSummary(items);
    assert.equal(summary.complete, false);
    assert.match(summary.body, /Results unavailable/);
    assert.doesNotMatch(summary.body, /NaN|undefined|Sensitive internal detail/);
  }
  const absent = retentionSummary([]);
  assert.equal(absent.complete, false);
  assert.doesNotMatch(absent.body, /\| Metric/);
});

test("zero baseline costs have explicit comparisons rather than infinity", () => {
  const items = reports();
  items[0].data.phases.baseline_backlog.wal_bytes_including_ingestion = 0;
  items[0].data.phases.current_backlog.wal_bytes_including_ingestion = 0;
  items[1].data.phases.baseline_backlog.wal_bytes_including_ingestion = 0;
  const summary = retentionSummary(items);
  assert.match(summary.body, /0\.000 MB \| 0\.000 MB \| \+0\.00%/);
  assert.match(summary.body, /0\.000 MB \| 1\.000 MB \| n\/a/);
  assert.doesNotMatch(summary.body, /Infinity/);
});

test("CLI reads actual report files and writes artifact plus job summary", () => {
  const root = mkdtempSync(join(tmpdir(), "retention-report-"));
  const directory = join(root, "reports", "retention");
  try {
    mkdirSync(directory, { recursive: true });
    for (const item of reports())
      writeFileSync(join(directory, `${item.articles}.json`), JSON.stringify(item.data));
    const output = join(directory, "summary.json");
    const jobSummary = join(root, "job-summary.md");
    const command = join(root, "scripts", "ci", "job-retention-report.mjs");
    mkdirSync(join(root, "scripts", "ci"), { recursive: true });
    copyFileSync(new URL("./job-retention-report.mjs", import.meta.url), command);
    execFileSync(process.execPath, [command], {
      env: { ...process.env, GITHUB_STEP_SUMMARY: jobSummary },
    });
    assert.deepEqual(JSON.parse(readFileSync(output, "utf8")), retentionSummary(reports()));
    assert.match(readFileSync(jobSummary, "utf8"), /250,000 articles/);
    execFileSync(process.execPath, [command], {
      env: { ...process.env, GITHUB_STEP_SUMMARY: "" },
    });
    const previous = process.env.GITHUB_STEP_SUMMARY;
    try {
      process.env.GITHUB_STEP_SUMMARY = jobSummary;
      assert.equal(retentionReportMain([], root).complete, true);
    } finally {
      if (previous === undefined) delete process.env.GITHUB_STEP_SUMMARY;
      else process.env.GITHUB_STEP_SUMMARY = previous;
    }
    writeFileSync(join(directory, "100000.json"), "invalid JSON");
    assert.equal(retentionSummary(readRetentionReports(directory)).complete, false);
    rmSync(join(directory, "100000.json"));
    symlinkSync(join(directory, "250000.json"), join(directory, "100000.json"));
    assert.equal(readRetentionReports(directory)[1].data, undefined);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
  assert.equal(
    retentionSummary(readRetentionReports("/missing/retention-reports")).complete,
    false,
  );
});

test("report CLI rejects outside and traversal selectors before opening any path", () => {
  const root = mkdtempSync(join(tmpdir(), "retention-cli-boundary-"));
  const outside = join(root, "outside.json");
  writeFileSync(outside, "unchanged");
  const opened = mock.method(fs, "openSync", () => {
    throw new Error("Unexpected filesystem access");
  });
  try {
    for (const arguments_ of [
      ["../private", outside],
      ["/etc", outside],
      ["--output", outside],
    ])
      assert.throws(() => retentionReportMain(arguments_, root), /accepts no path arguments/);
    assert.equal(opened.mock.callCount(), 0);
  } finally {
    opened.mock.restore();
  }
  try {
    const command = fileURLToPath(new URL("./job-retention-report.mjs", import.meta.url));
    assert.throws(
      () => execFileSync(process.execPath, [command, "../private", outside], { stdio: "pipe" }),
      /accepts no path arguments/,
    );
    assert.equal(readFileSync(outside, "utf8"), "unchanged");
    assert.equal(fs.existsSync(join(root, "reports")), false);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("report directory components and summary links cannot escape the workspace", () => {
  for (const scenario of ["root", "root-trailing", "reports", "retention", "summary", "hardlink"]) {
    const root = mkdtempSync(join(tmpdir(), "retention-path-boundary-"));
    const outside = mkdtempSync(join(tmpdir(), "retention-outside-"));
    const sentinel = join(outside, "summary.json");
    writeFileSync(sentinel, "unchanged");
    let target = root;
    if (scenario.startsWith("root")) {
      target = join(root, "linked-root");
      symlinkSync(outside, target);
      if (scenario === "root-trailing") target += "/";
    } else if (scenario === "reports") {
      symlinkSync(outside, join(root, "reports"));
    } else if (scenario === "retention") {
      mkdirSync(join(root, "reports"));
      symlinkSync(outside, join(root, "reports", "retention"));
    } else {
      const directory = seedReports(root);
      if (scenario === "summary") symlinkSync(sentinel, join(directory, "summary.json"));
      else linkSync(sentinel, join(directory, "summary.json"));
    }
    const reads = mock.method(fs, "readSync", () => {
      throw new Error("Unexpected report read");
    });
    try {
      assert.throws(() => runReport(target), /ELOOP|ENOTDIR|Invalid summary output/);
      assert.equal(reads.mock.callCount(), 0);
      assert.equal(readFileSync(sentinel, "utf8"), "unchanged");
      assert.equal(fs.existsSync(join(outside, "100000.json")), false);
    } finally {
      reads.mock.restore();
      rmSync(root, { recursive: true, force: true });
      rmSync(outside, { recursive: true, force: true });
    }
  }
});

test("report inputs reject symlinks and hardlinks while saving an explicit incomplete result", () => {
  for (const kind of ["symlink", "hardlink"]) {
    const root = mkdtempSync(join(tmpdir(), "retention-input-boundary-"));
    const directory = seedReports(root);
    const outside = join(root, "outside.json");
    const contents = JSON.stringify(measurement(100000));
    writeFileSync(outside, contents);
    rmSync(join(directory, "100000.json"));
    if (kind === "symlink") symlinkSync(outside, join(directory, "100000.json"));
    else linkSync(outside, join(directory, "100000.json"));
    try {
      assert.equal(runReport(root).complete, false);
      assert.equal(JSON.parse(readFileSync(join(directory, "summary.json"))).complete, false);
      assert.equal(readFileSync(outside, "utf8"), contents);
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  }
});

test("an opened report directory stays confined when its pathname is replaced", () => {
  const root = mkdtempSync(join(tmpdir(), "retention-directory-race-"));
  const outside = mkdtempSync(join(tmpdir(), "retention-directory-outside-"));
  const directory = seedReports(root);
  const retained = `${directory}-retained`;
  const open = fs.openSync;
  let replaced = false;
  const replacement = mock.method(fs, "openSync", (path, flags, mode) => {
    const descriptor = open(path, flags, mode);
    if (!replaced && String(path).endsWith("/summary.json")) {
      replaced = true;
      fs.renameSync(directory, retained);
      symlinkSync(outside, directory);
    }
    return descriptor;
  });
  try {
    assert.equal(runReport(root).complete, true);
    assert.equal(JSON.parse(readFileSync(join(retained, "summary.json"))).complete, true);
    assert.deepEqual(fs.readdirSync(outside), []);
  } finally {
    replacement.mock.restore();
    rmSync(root, { recursive: true, force: true });
    rmSync(outside, { recursive: true, force: true });
  }
});

test("fresh directories are created and failed setup closes every opened descriptor", () => {
  const root = mkdtempSync(join(tmpdir(), "retention-directory-setup-"));
  const descriptors = [];
  const open = fs.openSync;
  const opened = mock.method(fs, "openSync", (...arguments_) => {
    const descriptor = open(...arguments_);
    descriptors.push(descriptor);
    return descriptor;
  });
  try {
    assert.equal(runReport(root).complete, false);
    assert.equal(
      JSON.parse(readFileSync(join(root, "reports", "retention", "summary.json"))).complete,
      false,
    );
    for (const descriptor of descriptors)
      assert.throws(() => fs.fstatSync(descriptor), { code: "EBADF" });
    const failed = mock.method(fs, "mkdirSync", () => {
      throw Object.assign(new Error("Permission denied"), { code: "EACCES" });
    });
    try {
      assert.throws(() => runReport(root), /Permission denied/);
      assert.throws(() => fs.fstatSync(descriptors.at(-1)), { code: "EBADF" });
    } finally {
      failed.mock.restore();
    }
  } finally {
    opened.mock.restore();
    rmSync(root, { recursive: true, force: true });
  }
});

test("a nonregular summary is rejected before reading inputs and all descriptors close", () => {
  const root = mkdtempSync(join(tmpdir(), "retention-summary-fifo-"));
  const directory = seedReports(root);
  const path = join(directory, "summary.json");
  execFileSync("mkfifo", [path]);
  const reader = fs.openSync(path, fs.constants.O_RDONLY | fs.constants.O_NONBLOCK);
  try {
    assert.throws(() => runReport(root), /Invalid summary output/);
  } finally {
    fs.closeSync(reader);
    rmSync(root, { recursive: true, force: true });
  }
});

test("report reader keeps the opened inode when the pathname is replaced after fstat", () => {
  const directory = mkdtempSync(join(tmpdir(), "retention-replacement-"));
  const path = join(directory, "report.json");
  const replacement = join(directory, "replacement.json");
  writeFileSync(path, "original");
  writeFileSync(replacement, "replacement");
  const open = fs.openSync;
  const stat = fs.fstatSync;
  let opened;
  const openMock = mock.method(fs, "openSync", (file, flags) => {
    assert.equal(file, path);
    assert.equal(flags, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW | fs.constants.O_NONBLOCK);
    opened = open(file, flags);
    return opened;
  });
  const statMock = mock.method(fs, "fstatSync", (descriptor) => {
    assert.equal(descriptor, opened);
    const result = stat(descriptor);
    fs.renameSync(replacement, path);
    return result;
  });
  try {
    assert.equal(readBoundedReport(path, 32), "original");
    assert.throws(() => stat(opened), { code: "EBADF" });
  } finally {
    openMock.mock.restore();
    statMock.mock.restore();
    assert.equal(readFileSync(path, "utf8"), "replacement");
    rmSync(directory, { recursive: true, force: true });
  }
});

test("report reader caps growth after fstat and closes descriptors on validation and IO errors", () => {
  for (const scenario of [
    "growth",
    "shrink",
    "stat-error",
    "read-error",
    "empty",
    "oversize",
    "directory",
  ]) {
    const directory = mkdtempSync(join(tmpdir(), "retention-bounded-"));
    const path = scenario === "directory" ? directory : join(directory, "report.json");
    if (scenario !== "directory")
      writeFileSync(
        path,
        scenario === "empty" ? "" : scenario === "oversize" ? "x".repeat(33) : "safe",
      );
    const open = fs.openSync;
    const stat = fs.fstatSync;
    let opened;
    const openMock = mock.method(fs, "openSync", (file, flags) => {
      opened = open(file, flags);
      return opened;
    });
    const statMock = mock.method(fs, "fstatSync", (descriptor) => {
      if (scenario === "stat-error") throw new Error("Stat failed");
      const result = stat(descriptor);
      if (scenario === "growth") writeFileSync(path, "x".repeat(33));
      if (scenario === "shrink") writeFileSync(path, "");
      return result;
    });
    const readMock =
      scenario === "read-error"
        ? mock.method(fs, "readSync", () => {
            throw new Error("Read failed");
          })
        : undefined;
    try {
      assert.throws(() => readBoundedReport(path, 32), /Invalid report|Stat failed|Read failed/);
      assert.throws(() => stat(opened), { code: "EBADF" });
    } finally {
      openMock.mock.restore();
      statMock.mock.restore();
      readMock?.mock.restore();
      rmSync(directory, { recursive: true, force: true });
    }
  }
});

test("report reader handles partial reads and rejects missing files and symlinks before opening", () => {
  const directory = mkdtempSync(join(tmpdir(), "retention-partial-"));
  const path = join(directory, "report.json");
  const link = join(directory, "link.json");
  writeFileSync(path, "safe data");
  symlinkSync(path, link);
  const read = fs.readSync;
  const readMock = mock.method(fs, "readSync", (descriptor, buffer, offset, length, position) =>
    read(descriptor, buffer, offset, Math.min(length, 2), position),
  );
  try {
    assert.equal(readBoundedReport(path, 32), "safe data");
    assert.throws(() => readBoundedReport(link, 32), { code: "ELOOP" });
    assert.throws(() => readBoundedReport(join(directory, "missing.json"), 32), { code: "ENOENT" });
  } finally {
    readMock.mock.restore();
    rmSync(directory, { recursive: true, force: true });
  }
});
