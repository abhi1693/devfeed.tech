export const runs = 3;

// Use medians for timings; every run must stay within transfer budgets.
const timing = (maxNumericValue) => ["error", { maxNumericValue, aggregationMethod: "median" }];
const size = (maxNumericValue) => ["error", { maxNumericValue, aggregationMethod: "pessimistic" }];

export function lighthouseConfig(app, urls, chromePath, outputDir) {
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
          ...(app === "admin"
            ? { preset: "desktop", extraHeaders: { Cookie: "devfeed_admin_session=fixture" } }
            : {}),
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
                app === "admin"
                  ? 2500
                  : new URL(url).pathname.startsWith("/articles/")
                    ? 5500
                    : 4000,
              ),
              "cumulative-layout-shift": timing(0.1),
              "total-blocking-time": timing(app === "web" ? 400 : 300),
              "resource-summary:script:size": size((app === "web" ? 330 : 480) * 1024),
              "resource-summary:stylesheet:size": size((app === "web" ? 32 : 20) * 1024),
              "resource-summary:image:size": size((app === "web" ? 680 : 16) * 1024),
              "resource-summary:total:size": size((app === "web" ? 1050 : 600) * 1024),
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
