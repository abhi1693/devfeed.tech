const profiles = {
  unit: { runs: 2_000, modelRuns: 20, maxCommands: 20 },
  pr: { runs: 1_000, modelRuns: 60, maxCommands: 30 },
  nightly: { runs: 10_000, modelRuns: 400, maxCommands: 100 },
};

export function propertySettings(env = process.env) {
  const mode = env.DEVFEED_PROPERTY_MODE ?? "unit";
  if (!Object.hasOwn(profiles, mode)) throw new Error(`Invalid property mode: ${mode}`);
  const rawSeed = env.DEVFEED_FUZZ_SEED;
  if (
    rawSeed !== undefined &&
    (!/^-?\d+$/.test(rawSeed) || Number(rawSeed) < -(2 ** 31) || Number(rawSeed) > 2 ** 31 - 1)
  )
    throw new Error("DEVFEED_FUZZ_SEED must be a signed 32-bit integer");
  const path = env.DEVFEED_FUZZ_PATH;
  const replayPath = env.DEVFEED_FUZZ_REPLAY_PATH;
  if ((path !== undefined || replayPath !== undefined) && rawSeed === undefined)
    throw new Error("A shrinking or command replay path requires DEVFEED_FUZZ_SEED");
  if (path !== undefined && !/^\d+(?::\d+)*$/.test(path))
    throw new Error("Invalid DEVFEED_FUZZ_PATH");
  return {
    mode,
    ...profiles[mode],
    ...(rawSeed !== undefined ? { seed: Number(rawSeed) } : {}),
    ...(path !== undefined ? { path } : {}),
    ...(replayPath !== undefined ? { replayPath } : {}),
  };
}

export function propertyOptions(stateful = false) {
  const { runs, modelRuns, seed, path } = propertySettings();
  return {
    numRuns: stateful ? modelRuns : runs,
    ...(seed !== undefined ? { seed } : {}),
    ...(path !== undefined ? { path } : {}),
  };
}

export function commandOptions() {
  const { maxCommands, replayPath } = propertySettings();
  /** @type {import("fast-check").CommandsContraints} */
  const options = { maxCommands, size: "max", ...(replayPath !== undefined ? { replayPath } : {}) };
  return options;
}
