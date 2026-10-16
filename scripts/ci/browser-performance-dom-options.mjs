/** Parse benchmark modes without starting builds, servers or browsers. */
export function browserPerformanceOptions(args, root, runs) {
  const domPhase = args.find((arg) => arg.startsWith("--reader-dom="))?.split("=")[1];
  if (domPhase && !["before", "after"].includes(domPhase))
    throw new Error("Reader DOM phase must be before or after");
  const articleEntryPhase = args.find((arg) => arg.startsWith("--article-entry="))?.split("=")[1];
  if (articleEntryPhase && !["before", "after"].includes(articleEntryPhase))
    throw new Error("Article entry phase must be before or after");
  return {
    domPhase,
    articleEntryPhase,
    measurementRuns: articleEntryPhase ? 5 : runs,
    output: domPhase
      ? `${root}/reports/reader-dom/${domPhase}`
      : articleEntryPhase
        ? `${root}/reports/article-entry/${articleEntryPhase}`
        : `${root}/reports/browser-performance`,
    skipBuild: args.includes("--skip-build"),
  };
}
