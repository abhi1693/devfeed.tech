import { mkdir, mkdtemp, realpath, stat } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repository = fileURLToPath(new URL("../../", import.meta.url));
const runtimes = new Set(["web", "chrome", "edge"]);

/** Retain review evidence in a uniquely allocated, owner-only directory for this run. */
export async function createReaderArtifactDirectory(
  runtime,
  { parentDirectory, ci = Boolean(process.env.CI) } = {},
) {
  if (!runtimes.has(runtime)) throw new TypeError("Unsupported reader artifact runtime");
  if (parentDirectory !== undefined && (typeof parentDirectory !== "string" || !parentDirectory))
    throw new TypeError("A reader artifact parent must be a directory path");
  const parent = parentDirectory
    ? path.resolve(parentDirectory)
    : ci
      ? path.join(repository, "reports", "reader-artifacts")
      : tmpdir();
  if (parentDirectory || ci) await mkdir(parent, { recursive: true, mode: 0o700 });
  const canonical = await realpath(parent);
  if (parentDirectory && process.getuid) {
    const metadata = await stat(canonical);
    if (
      (metadata.uid !== process.getuid() && metadata.uid !== 0) ||
      ((metadata.mode & 0o002) !== 0 && (metadata.mode & 0o1000) === 0)
    )
      throw new Error(
        "Reader artifact parents must not be owned by another user or world-writable",
      );
  }
  // mkdtemp atomically creates a random directory with mode 0700; no predictable file is opened.
  const directory = await mkdtemp(path.join(canonical, `devfeed-reader-${runtime}-`));
  console.log(`Reader browser artifacts (${runtime}): ${directory}`);
  return directory;
}
