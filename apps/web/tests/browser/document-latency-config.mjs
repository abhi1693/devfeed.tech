export function documentApiUrl(value, upstream) {
  if (typeof value !== "string" || !value.startsWith("/v1/"))
    throw new Error("Only public API paths are accepted by the benchmark proxy");
  const incoming = new URL(value, "http://fixture.invalid");
  if (!incoming.pathname.startsWith("/v1/")) throw new Error("Invalid public API path");
  const target = new URL(upstream);
  if (!["http:", "https:"].includes(target.protocol) || target.username || target.password)
    throw new Error("Set a public HTTP API origin without credentials");
  target.pathname = incoming.pathname;
  target.search = incoming.search;
  target.hash = "";
  return target;
}

export function documentReportFilename(phase) {
  switch (phase) {
    case "before":
      return "before.json";
    case "after":
      return "after.json";
    case "profile":
      return "profile.json";
    default:
      throw new Error("Document benchmark phase must be before, after or profile");
  }
}

export function documentResponseStatus(value) {
  const status = Number(value);
  if (!Number.isInteger(status) || status < 100 || status > 599)
    throw new Error("Invalid HTTP status in benchmark response");
  return status;
}

export function documentCacheStatus(value) {
  switch (value) {
    case "HIT":
      return "HIT";
    case "MISS":
      return "MISS";
    case "BYPASS":
      return "BYPASS";
    case null:
      return null;
    default:
      return "UNKNOWN";
  }
}
