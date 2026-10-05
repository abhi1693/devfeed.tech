export const propertySuites = {
  "pagination.property.test.ts": 3,
  "urls.property.test.ts": 4,
  "follows.property.test.tsx": 4,
  "telemetry-fuzz.test.ts": 4,
};

export function checkPropertyReport(report) {
  const count = Object.values(propertySuites).reduce((total, value) => total + value, 0);
  if (!report.success || report.numTotalTests !== count || report.numPassedTests !== count)
    throw new Error(`Expected ${count} passing properties`);
  const suites = report.testResults;
  if (!Array.isArray(suites) || suites.length !== Object.keys(propertySuites).length)
    throw new Error("Missing or unexpected property suites");
  const seen = new Set();
  for (const suite of suites) {
    const name = suite.name.replaceAll("\\", "/").split("/").at(-1);
    if (!Object.hasOwn(propertySuites, name) || seen.has(name))
      throw new Error(`Unexpected or duplicate property suite: ${name}`);
    seen.add(name);
    if (
      suite.status !== "passed" ||
      suite.assertionResults?.length !== propertySuites[name] ||
      suite.assertionResults.some((test) => test.status !== "passed" || !test.fullName) ||
      new Set(suite.assertionResults.map((test) => test.fullName)).size !== propertySuites[name]
    )
      throw new Error(`Failed, skipped or missing properties: ${name}`);
  }
}
