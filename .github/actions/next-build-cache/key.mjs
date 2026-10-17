import { createHash } from "node:crypto";
import { appendFile, lstat, readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

const apps = new Set(["web", "admin", "partner"]);
const runtime = {
  platform: process.platform,
  arch: process.arch,
  node: process.versions.node,
};

async function configuration(root, filename, required = false) {
  const path = resolve(root, filename);
  let stat;
  try {
    stat = await lstat(path);
  } catch (error) {
    if (!required && error.code === "ENOENT") return null;
    throw error;
  }
  if (!stat.isFile()) throw new Error(`Build cache input must be a regular file: ${filename}`);
  return readFile(path);
}

export async function nextBuildCachePlan({ root, app, profile, source, environment = runtime }) {
  if (!apps.has(app)) throw new Error("Next cache app must be web, admin, or partner");
  if (typeof profile !== "string" || !/^[a-z][a-z0-9-]{0,47}$/.test(profile)) {
    throw new Error("Next cache profile must be a lowercase name of at most 48 characters");
  }
  if (typeof source !== "string" || !/^[a-f0-9]{40}$/.test(source)) {
    throw new Error("Next cache source must be the full GitHub commit SHA");
  }
  for (const field of ["platform", "arch", "node"]) {
    if (!/^[a-zA-Z0-9.-]+$/.test(environment[field] ?? "")) {
      throw new Error(`Invalid Next cache runtime ${field}`);
    }
  }

  const lock = await configuration(root, "package-lock.json", true);
  const dependencies = createHash("sha256").update(lock).digest("hex");
  const config = createHash("sha256");
  for (const [filename, required] of [
    ["package.json", true],
    ["tsconfig.json", false],
    ["tsconfig.base.json", false],
    [`apps/${app}/package.json`, true],
    [`apps/${app}/tsconfig.json`, true],
    [`apps/${app}/next.config.ts`, true],
    [`apps/${app}/postcss.config.mjs`, false],
    [`apps/${app}/postcss.config.js`, false],
    [`apps/${app}/tailwind.config.ts`, false],
    [`apps/${app}/tailwind.config.js`, false],
  ]) {
    const content = await configuration(root, filename, required);
    config.update(JSON.stringify([filename, content?.toString("base64") ?? null]));
  }
  const compatibility = config.digest("hex");
  const restorePrefix = [
    "devfeed-next-v1",
    environment.platform,
    environment.arch,
    `node${environment.node}`,
    app,
    profile,
    dependencies,
    compatibility,
    "",
  ].join("-");
  const cache = `apps/${app}/.next/cache`;
  return {
    key: `${restorePrefix}${source}`,
    restorePrefix,
    paths: [`${cache}/turbopack`, `${cache}/webpack`, `${cache}/swc`, `${cache}/.tsbuildinfo`],
  };
}

async function main() {
  const output = process.env.GITHUB_OUTPUT;
  const root = process.env.GITHUB_WORKSPACE;
  if (!output || !root) throw new Error("Next cache requires GITHUB_OUTPUT and GITHUB_WORKSPACE");
  const plan = await nextBuildCachePlan({
    root,
    app: process.env.NEXT_CACHE_APP,
    profile: process.env.NEXT_CACHE_PROFILE,
    source: process.env.GITHUB_SHA,
  });
  await appendFile(
    output,
    `key=${plan.key}\nrestore-prefix=${plan.restorePrefix}\npaths<<NEXT_COMPILER_CACHE_PATHS\n${plan.paths.join("\n")}\nNEXT_COMPILER_CACHE_PATHS\n`,
  );
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  await main();
}
