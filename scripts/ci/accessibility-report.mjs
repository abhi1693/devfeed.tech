import { appendFile, mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

export async function writeAccessibilitySummary(
  directory = path.resolve("reports/accessibility"),
  stepSummary = process.env.GITHUB_STEP_SUMMARY,
) {
  await mkdir(directory, { recursive: true });
  const lines = [
    "### Reader accessibility",
    "",
    "| Runtime | Session | Result | Axe scans | Keyboard/layout checks | Violations | Needs manual review |",
    "| --- | --- | --- | ---: | ---: | ---: | ---: |",
  ];
  let failed = false;
  for (const surface of ["web", "chrome", "edge"]) {
    for (const state of ["guest", "signed-in"]) {
      try {
        const report = JSON.parse(
          await readFile(path.join(directory, surface, state, "summary.json"), "utf8"),
        );
        const violations = report.scans.reduce((sum, scan) => sum + scan.violations.length, 0);
        const incomplete = report.scans.reduce((sum, scan) => sum + scan.incomplete, 0);
        const passed =
          report.status === "passed" &&
          violations === 0 &&
          report.scans.length > 0 &&
          report.interactions.length > 0 &&
          report.interactions.every((step) => step.status === "passed");
        failed ||= !passed;
        lines.push(
          `| ${surface} | ${state} | ${passed ? "PASS" : "FAIL"} | ${report.scans.length} | ${report.interactions.filter((step) => step.status === "passed").length} | ${violations} | ${incomplete} |`,
        );
      } catch {
        failed = true;
        lines.push(`| ${surface} | ${state} | MISSING | — | — | — | — |`);
      }
    }
  }
  lines.push(
    "",
    "Axe WCAG 2.0/2.1/2.2 A/AA checks cover contrast, accessible names, labels and ARIA. Keyboard checks cover skip navigation, layout selection, bookmarks/sign-in and preview focus/escape. Layout checks include narrow screens, 200% zoom-equivalent reflow and 200% text resizing, plus reduced-motion previews.",
    "",
    "Full per-scan JSON includes failing selectors, remediation links and incomplete checks requiring manual review. Automated results do not establish WCAG compliance. Reports and failure screenshots are in the reader-parity artifact.",
  );
  const summary = lines.join("\n") + "\n";
  await writeFile(path.join(directory, "summary.md"), summary);
  if (stepSummary) await appendFile(stepSummary, summary);
  return { summary, failed };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const { summary, failed } = await writeAccessibilitySummary();
  console.log(summary);
  if (failed) process.exitCode = 1;
}
