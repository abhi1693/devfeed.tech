import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

function safeUrl(value) {
  try {
    const url = new URL(value);
    if (!["http:", "https:", "chrome-extension:"].includes(url.protocol)) return null;
    return `${url.protocol}//${url.host}${url.pathname}`.slice(0, 500);
  } catch {
    return null;
  }
}

function fixtureImage(value) {
  try {
    const url = new URL(value);
    return url.origin === "https://images.example.test" ? safeUrl(value) : null;
  } catch {
    return null;
  }
}

function appendBounded(entries, value, limit = 128) {
  entries.push(value);
  if (entries.length > limit) entries.shift();
}

function initiatorDetails(initiator) {
  const frames = [];
  for (let stack = initiator.stack; stack && frames.length < 12; stack = stack.parent) {
    for (const frame of stack.callFrames ?? []) {
      if (frames.length === 12) break;
      frames.push({
        functionName: frame.functionName.slice(0, 150),
        url: safeUrl(frame.url),
        lineNumber: frame.lineNumber,
        columnNumber: frame.columnNumber,
      });
    }
  }
  return {
    type: initiator.type,
    url: safeUrl(initiator.url),
    lineNumber: initiator.lineNumber,
    columnNumber: initiator.columnNumber,
    frames,
  };
}

function documentMetadata() {
  const clean = (value) => {
    try {
      const url = new URL(value);
      if (!["http:", "https:", "chrome-extension:"].includes(url.protocol)) return null;
      return `${url.protocol}//${url.host}${url.pathname}`.slice(0, 500);
    } catch {
      return null;
    }
  };
  return {
    url: clean(location.href),
    links: Array.from(document.querySelectorAll("link[href]"))
      .slice(0, 32)
      .map((link) => ({ href: clean(link.href), rel: link.rel, as: link.as })),
    styleSheets: Array.from(document.styleSheets)
      .slice(0, 32)
      .map((sheet) => ({ href: clean(sheet.href), disabled: sheet.disabled })),
  };
}

// Only fixture-image metadata is retained: no headers, bodies, URL credentials, or queries.
// The reader fixture's existing fatal console assertion still fails the journey.
export async function fixtureImageDiagnostics(context) {
  // observedAt uses Node epoch milliseconds; browser timestamp fields name their clock.
  const requests = [];
  const failures = [];
  const violations = [];
  const consoleLocations = [];
  const browserLogs = [];
  const browserIssues = [];
  const diagnosticErrors = [];
  let failureDocument;
  const noteError = (error) => appendBounded(diagnosticErrors, String(error).slice(0, 300), 8);

  await context.exposeBinding("__devfeedFixtureImageViolation", ({ page }, event) => {
    if (!fixtureImage(event.blockedURI)) return;
    appendBounded(
      violations,
      {
        blockedURI: fixtureImage(event.blockedURI),
        currentURL: safeUrl(page.url()),
        sourceFile: safeUrl(event.sourceFile),
        lineNumber: event.lineNumber,
        columnNumber: event.columnNumber,
        effectiveDirective: event.effectiveDirective,
        disposition: event.disposition,
      },
      32,
    );
    failureDocument ??= page.evaluate(documentMetadata).catch(noteError);
  });
  await context.addInitScript(() => {
    document.addEventListener("securitypolicyviolation", (event) => {
      let blockedURL;
      try {
        blockedURL = new URL(event.blockedURI);
      } catch {
        return;
      }
      if (blockedURL.origin !== "https://images.example.test") return;
      void window
        .__devfeedFixtureImageViolation({
          blockedURI: event.blockedURI,
          sourceFile: event.sourceFile,
          lineNumber: event.lineNumber,
          columnNumber: event.columnNumber,
          effectiveDirective: event.effectiveDirective,
          disposition: event.disposition,
        })
        .catch(() => undefined); // Navigation can close the originating document.
    });
  });

  let pageIndex = 0;
  async function attach(page) {
    const index = pageIndex++;
    page.on("console", (message) => {
      if (message.type() !== "error") return;
      const urls =
        message
          .text()
          .slice(0, 4096)
          .match(/https:\/\/[^\s"'<>]+/g) ?? [];
      if (!urls.some((value) => fixtureImage(value))) return;
      const location = message.location();
      appendBounded(
        consoleLocations,
        {
          pageIndex: index,
          observedAt: Date.now(),
          currentURL: safeUrl(page.url()),
          ...location,
          url: safeUrl(location.url),
        },
        32,
      );
    });
    const session = await context.newCDPSession(page);
    let lastFixtureRequestFrameId;
    session.on("Network.requestWillBeSent", (event) => {
      const url = fixtureImage(event.request.url);
      if (!url) return;
      lastFixtureRequestFrameId = event.frameId;
      appendBounded(requests, {
        pageIndex: index,
        observedAt: Date.now(),
        networkMonotonicSeconds: event.timestamp,
        frameId: event.frameId,
        loaderId: event.loaderId,
        requestId: `${index}:${event.requestId}`,
        url,
        currentURL: safeUrl(page.url()),
        resourceType: event.type,
        initiator: initiatorDetails(event.initiator),
      });
    });
    session.on("Network.loadingFailed", (event) => {
      const request = requests.findLast(
        (entry) => entry.requestId === `${index}:${event.requestId}`,
      );
      if (request)
        appendBounded(failures, {
          ...request,
          observedAt: Date.now(),
          networkMonotonicSeconds: event.timestamp,
          blockedReason: event.blockedReason,
          errorText: event.errorText,
          canceled: event.canceled,
        });
    });
    session.on("Log.entryAdded", ({ entry }) => {
      const urls = entry.text.slice(0, 4096).match(/https:\/\/[^\s"'<>]+/g) ?? [];
      const blockedURLs = urls.map(fixtureImage).filter(Boolean);
      if (!blockedURLs.length) return;
      appendBounded(
        browserLogs,
        {
          pageIndex: index,
          observedAt: Date.now(),
          logEpochMilliseconds: entry.timestamp,
          lastFixtureRequestFrameId,
          currentURL: safeUrl(page.url()),
          source: entry.source,
          level: entry.level,
          blockedURLs,
          url: safeUrl(entry.url),
          lineNumber: entry.lineNumber,
          networkRequestId: entry.networkRequestId,
          frames: initiatorDetails({ stack: entry.stackTrace }).frames,
        },
        32,
      );
      failureDocument ??= page.evaluate(documentMetadata).catch(noteError);
    });
    session.on("Audits.issueAdded", ({ issue }) => {
      const detail = issue.details.contentSecurityPolicyIssueDetails;
      const blockedURL = fixtureImage(detail?.blockedURL);
      if (!blockedURL) return;
      appendBounded(
        browserIssues,
        {
          pageIndex: index,
          observedAt: Date.now(),
          lastFixtureRequestFrameId,
          currentURL: safeUrl(page.url()),
          code: issue.code,
          blockedURL,
          violatedDirective: detail.violatedDirective,
          isReportOnly: detail.isReportOnly,
          violationType: detail.contentSecurityPolicyViolationType,
          sourceCodeLocation: detail.sourceCodeLocation
            ? {
                url: safeUrl(detail.sourceCodeLocation.url),
                lineNumber: detail.sourceCodeLocation.lineNumber,
                columnNumber: detail.sourceCodeLocation.columnNumber,
              }
            : undefined,
        },
        32,
      );
      failureDocument ??= page.evaluate(documentMetadata).catch(noteError);
    });
    await session.send("Log.enable");
    await session.send("Audits.enable");
    await session.send("Network.enable");
  }
  context.on("page", (page) => void attach(page).catch(noteError));
  await Promise.all(context.pages().map((page) => attach(page).catch(noteError)));

  return {
    async writeFailure(output) {
      try {
        const pages = await Promise.all(
          context
            .pages()
            .slice(-8)
            .map(async (page) => {
              try {
                return await page.evaluate(documentMetadata);
              } catch (error) {
                noteError(error);
                return { url: safeUrl(page.url()), unavailable: true };
              }
            }),
        );
        const firstViolationDocument = await failureDocument;
        await mkdir(path.dirname(output), { recursive: true });
        await writeFile(
          output,
          JSON.stringify(
            {
              requests,
              failures,
              violations,
              consoleLocations,
              browserLogs,
              browserIssues,
              firstViolationDocument,
              pages,
              diagnosticErrors,
            },
            null,
            2,
          ),
        );
      } catch (error) {
        // A diagnostic failure must never replace the original journey failure.
        console.error(`Fixture image diagnostics unavailable: ${String(error)}`);
      }
    },
  };
}
