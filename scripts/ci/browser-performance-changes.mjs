import { appendFileSync, readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { pathToFileURL } from "node:url";

export function affectsBrowser(paths) {
  return paths.some(
    (path) =>
      /^(apps\/(web|admin|extensions)\/|packages\/(ui|theme|telemetry)\/)/.test(path) ||
      /^(package(-lock)?\.json|\.npmrc|\.github\/workflows\/ci\.yml)$/.test(path) ||
      /^scripts\/(assets\/|ci\/browser-performance|testing\/)/.test(path),
  );
}

export function changedPaths(base, head) {
  if (![base, head].every((sha) => /^[a-f0-9]{40}$/.test(sha)))
    throw new Error("Browser budgets require valid base and head commits");
  return execFileSync("/usr/bin/git", ["diff", "--name-only", "-z", base, head], {
    encoding: "utf8",
    timeout: 30_000,
  })
    .split("\0")
    .filter(Boolean);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const event = JSON.parse(readFileSync(process.env.GITHUB_EVENT_PATH, "utf8"));
  const base = event.pull_request?.base.sha ?? event.merge_group?.base_sha;
  const head = execFileSync("/usr/bin/git", ["rev-parse", "HEAD"], {
    encoding: "utf8",
    timeout: 30_000,
  }).trim();
  const needed = affectsBrowser(changedPaths(base, head));
  appendFileSync(process.env.GITHUB_OUTPUT, `needed=${needed}\n`);
  if (!needed)
    appendFileSync(process.env.GITHUB_STEP_SUMMARY, "Browser budgets: no frontend changes.\n");
}
