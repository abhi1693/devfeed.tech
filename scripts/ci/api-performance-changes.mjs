import { appendFileSync, readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { pathToFileURL } from "node:url";

const frontendDirectory =
  /^(apps\/(web|admin|extensions)\/|packages\/(ui|theme|telemetry|braces)\/)/;
const frontendFile =
  /\.(?:[cm]?[jt]sx?|css|scss|sass|less|html|json|svg|png|jpe?g|gif|webp|avif|ico|woff2?|ttf|otf)$/;
const pythonInput = /\.(?:py|pyi|sql)$|(?:^|\/)(?:pyproject\.toml|uv\.lock)$/;

export function affectsApi(paths) {
  return paths.some((path) => {
    if (typeof path !== "string" || pythonInput.test(path)) return true;
    if (frontendDirectory.test(path) && frontendFile.test(path)) return false;
    if (/^scripts\/testing\/.*\.mjs$/.test(path)) return false;
    return true;
  });
}

export function changedPaths(base, head) {
  if (![base, head].every((sha) => typeof sha === "string" && /^[a-f0-9]{40}$/.test(sha)))
    throw new Error("API performance requires valid base and head commits");
  return execFileSync("/usr/bin/git", ["diff", "--no-renames", "--name-only", "-z", base, head], {
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
    timeout: 30_000,
  })
    .split("\0")
    .filter(Boolean);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  if (!process.env.GITHUB_EVENT_PATH || !process.env.GITHUB_OUTPUT)
    throw new Error("API performance requires GITHUB_EVENT_PATH and GITHUB_OUTPUT");
  const event = JSON.parse(readFileSync(process.env.GITHUB_EVENT_PATH, "utf8"));
  if (!event.pull_request) throw new Error("API performance requires a pull request event");
  const needed = affectsApi(
    changedPaths(event.pull_request.base?.sha, event.pull_request.head?.sha),
  );
  appendFileSync(process.env.GITHUB_OUTPUT, `needed=${needed}\n`);
}
