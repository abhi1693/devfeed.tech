// Codex 0.154 SQLite diagnostics are independent of ephemeral analysis threads.
// Operate only on log databases; never open authentication or thread state.
const fs = require("node:fs");
const path = require("node:path");
const { DatabaseSync } = require("node:sqlite");

const MAX_ROWS = 2000;
const MAX_BYTES = 16 * 1024 * 1024;
const MAX_AGE_SECONDS = 24 * 60 * 60;

function retain(filename, now = Math.floor(Date.now() / 1000)) {
  const db = new DatabaseSync(filename);
  try {
    db.exec("PRAGMA busy_timeout = 1000");
    db.exec("PRAGMA temp_store = MEMORY");
    // A just-created database may not have completed its migrations yet.
    if (!db.prepare("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'logs'").get()) {
      return { initializing: true };
    }
    db.exec("BEGIN IMMEDIATE");
    try {
      db.prepare("DELETE FROM logs WHERE ts < ? OR level IN ('DEBUG', 'TRACE')")
        .run(now - MAX_AGE_SECONDS);
      const boundary = db.prepare("SELECT id FROM logs ORDER BY id DESC LIMIT 1 OFFSET ?")
        .get(MAX_ROWS - 1);
      if (boundary) db.prepare("DELETE FROM logs WHERE id < ?").run(boundary.id);
      // Count actual UTF-8 body bytes too, rather than trusting estimated_bytes.
      const oversized = db.prepare(`
        SELECT id FROM (
          SELECT id, SUM(MAX(COALESCE(estimated_bytes, 0),
            COALESCE(length(CAST(feedback_log_body AS BLOB)), 0)) + 512)
            OVER (ORDER BY id DESC) AS bytes
          FROM logs
        ) WHERE bytes > ? ORDER BY id DESC LIMIT 1
      `).get(MAX_BYTES);
      if (oversized) db.prepare("DELETE FROM logs WHERE id <= ?").run(oversized.id);
      db.exec("COMMIT");
    } catch (error) {
      db.exec("ROLLBACK");
      throw error;
    }
    // A row limit alone does not shrink a previously expanded SQLite file.
    const checkpoint = db.prepare("PRAGMA wal_checkpoint(TRUNCATE)").get();
    const pageSize = db.prepare("PRAGMA page_size").get().page_size;
    const freePages = db.prepare("PRAGMA freelist_count").get().freelist_count;
    if (checkpoint.busy === 0 && freePages * pageSize >= 8 * 1024 * 1024) {
      db.exec("VACUUM");
      db.exec("PRAGMA wal_checkpoint(TRUNCATE)");
    }
    return {
      rows: db.prepare("SELECT COUNT(*) AS count FROM logs").get().count,
      bytes: fs.statSync(filename).size,
      walBytes: fs.existsSync(`${filename}-wal`) ? fs.statSync(`${filename}-wal`).size : 0,
    };
  } finally {
    db.close();
  }
}

function maintain(directory) {
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    if (!entry.isFile() || !/^logs_\d+\.sqlite$/.test(entry.name)) continue;
    const result = retain(path.join(directory, entry.name));
    console.log(JSON.stringify({ event: "codex_log_retention", database: entry.name, ...result }));
  }
  fs.writeFileSync(path.join(directory, ".retention-health"), String(Date.now()), { mode: 0o600 });
}

function cleanupLegacy(directory) {
  // Recreate stops the sole writer before the deployment's init container runs.
  for (const name of ["logs_2.sqlite", "logs_2.sqlite-wal", "logs_2.sqlite-shm"]) {
    try {
      fs.unlinkSync(path.join(directory, name));
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
    }
  }
}

if (require.main === module) {
  const cleanup = process.argv[2] === "--cleanup-legacy";
  const directory = process.argv[cleanup ? 3 : 2];
  if (!directory || !path.isAbsolute(directory)) throw new Error("An absolute SQLite directory is required");
  if (cleanup) {
    cleanupLegacy(directory);
    console.log("Removed obsolete Codex diagnostic logs; authentication and other state preserved");
    process.exit(0);
  }
  const run = () => {
    try {
      maintain(directory);
    } catch (error) {
      console.error(JSON.stringify({ event: "codex_log_retention_failed", code: error.code || "unknown" }));
      // Leave the heartbeat stale so the sidecar's health checks expose failure.
    }
  };
  run();
  setInterval(run, 30000);
}

module.exports = { retain, maintain, cleanupLegacy, MAX_ROWS, MAX_BYTES, MAX_AGE_SECONDS };
