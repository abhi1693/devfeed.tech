export const runs = 3;

// Use medians for timings; every run must stay within transfer budgets.
const timing = (maxNumericValue) => ["error", { maxNumericValue, aggregationMethod: "median" }];
const size = (maxNumericValue) => ["error", { maxNumericValue, aggregationMethod: "pessimistic" }];

export function lighthouseConfig(urls, chromePath, outputDir) {
  // Keep baseline regression ceilings separate from the tighter LCP/TBT targets.
  return {
    ci: {
      collect: {
        url: urls,
        numberOfRuns: runs,
        chromePath,
        settings: {
          onlyCategories: ["performance", "best-practices"],
          chromeFlags: "--headless --no-sandbox --disable-dev-shm-usage",
        },
      },
      assert: {
        includePassedAssertions: true,
        assertMatrix: [
          ...urls.map((url) => ({
            matchingUrlPattern: `^${url.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}$`,
            assertions: {
              "first-contentful-paint": timing(1800),
              "largest-contentful-paint": timing(
                new URL(url).pathname.startsWith("/articles/") ? 5500 : 4000,
              ),
              "cumulative-layout-shift": timing(0.1),
              "total-blocking-time": timing(400),
              "resource-summary:script:size": size(330 * 1024),
              "resource-summary:stylesheet:size": size(32 * 1024),
              "resource-summary:image:size": size(680 * 1024),
              "resource-summary:total:size": size(1050 * 1024),
            },
          })),
          {
            matchingUrlPattern: ".*",
            assertions: {
              "largest-contentful-paint": [
                "warn",
                { maxNumericValue: 2500, aggregationMethod: "median" },
              ],
              "total-blocking-time": [
                "warn",
                { maxNumericValue: 200, aggregationMethod: "median" },
              ],
            },
          },
        ],
      },
      upload: { target: "filesystem", outputDir },
    },
  };
}
