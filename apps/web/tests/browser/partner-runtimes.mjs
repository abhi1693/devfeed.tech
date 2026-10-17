import assert from "node:assert/strict";

const platforms = ["web", "chrome", "edge"];
const focusedModes = new Set(["--search-context", "--link-rel", "--attribution", "--compact-feed"]);
const callerPaths = ["/v1/partner-placements/query", "/v1/partner-tracking/events"];

export function partnerRuntimes(arguments_ = []) {
  let selected;
  for (const argument of arguments_) {
    if (focusedModes.has(argument)) continue;
    const runtime = argument.startsWith("--runtime=") ? argument.slice("--runtime=".length) : null;
    if (!platforms.includes(runtime) || selected !== undefined) {
      throw new Error("Usage: partner-placements.mjs [--runtime=web|chrome|edge] [focused mode]");
    }
    selected = runtime;
  }
  return selected ? [selected] : [...platforms];
}

export function partnerRuntimePlan(arguments_ = []) {
  const selected = partnerRuntimes(arguments_);
  if (selected.length === platforms.length) {
    return selected.map((platform) => ({ platform, caller: platform, probe: false }));
  }
  const platform = selected[0];
  return [
    ...platforms.map((caller) => ({ platform, caller, probe: true })),
    { platform, caller: platform, probe: false },
  ];
}

export function assertPartnerCallerProofs(proofs, expectedIp) {
  for (const pathname of callerPaths) {
    assert.deepEqual(
      proofs.get(pathname),
      new Set([expectedIp]),
      `The same gateway signs only the current browser caller for ${pathname}`,
    );
  }
}
