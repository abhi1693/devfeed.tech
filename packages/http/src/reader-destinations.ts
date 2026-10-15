import contract from "./reader-destinations.json";

type ValuePolicy = {
  minLength?: number;
  maxLength?: number;
  pattern?: string;
  noSeparators?: boolean;
  values?: Record<string, string>;
};
type QueryPolicy = Omit<ValuePolicy, "values"> & {
  values?: readonly string[];
  format?: "date" | "integer";
  min?: number;
  max?: number;
};
type Segment = string | { name: string; type: string; static?: boolean };
export type ReaderNavigationDefinition =
  | { type: "article"; slugParameter: string }
  | { type: "profile"; usernameParameter: string }
  | { type: "local"; page: "catalog"; catalog: "topics" | "sources" }
  | { type: "local"; page: "settings"; settingsParameter: string }
  | {
      type: "local";
      page: "source-suggestion" | "mcp" | "mcp-authorize" | "leaderboard" | "dev-card";
    }
  | ({
      type: "reader";
      feed: "home" | "latest" | "search" | "bookmarks" | "trending" | "contextual";
      contentTypeParameter?: string;
    } & (
      | { collection?: undefined }
      | { collection: "topics" | "sources" | "tags"; slugParameter: string }
    ));
export type ReaderDestinationDefinition = {
  id: string;
  segments: readonly Segment[];
  aliases?: readonly { segments: readonly Segment[]; parameters: Record<string, string> }[];
  query: string;
  login: boolean;
  embedded: boolean;
  navigation?: ReaderNavigationDefinition;
  labels?: { telemetry: string; screen: string };
  page?: {
    section: "legal";
    title: string;
    description: string;
    updated: string;
    order: number;
  };
  sitemap?: {
    changefreq: "hourly" | "daily" | "monthly";
    priority: number;
  } & (
    | {
        kind: "page";
        publication: { source: "inventory"; key: string; parameter?: string } | { source: "page" };
      }
    | { kind: "inventory"; collection: string }
  );
};
type DestinationContract = {
  version: number;
  maxLength: number;
  parameters: Record<string, ValuePolicy>;
  queryFields: Record<string, QueryPolicy>;
  queryProfiles: Record<string, { fields: Record<string, string>; required?: readonly string[] }>;
  routes: readonly ReaderDestinationDefinition[];
};

export const readerDestinationContract = contract as DestinationContract;
export const readerContentTypeRoutes = contract.parameters.contentType.values;
export type ReaderContentType = keyof typeof readerContentTypeRoutes;
export const readerSettingsRoutes = contract.parameters.settings.values;
export type ReaderSettingsSection = keyof typeof readerSettingsRoutes;

/** The same bounded record is stored in authentication state and used by navigation. */
export type ReaderDestinationRecord = {
  routeId: string;
  parameters: Record<string, string>;
  query: Record<string, string>;
};
export type MatchedReaderDestination = {
  definition: ReaderDestinationDefinition;
  destination: ReaderDestinationRecord;
};

const unsafeText = /[\u0000-\u001f\u007f]/;
const unsafeURL = /[\u0000-\u0020\u007f\\]/;
const invalidEscape = /%(?![0-9a-fA-F]{2})/;
const separator = /[\/\\]/;

function validText(value: string, policy: ValuePolicy | QueryPolicy): boolean {
  if (!value.isWellFormed() || unsafeText.test(value)) return false;
  if (policy.minLength !== undefined && value.length < policy.minLength) return false;
  if (policy.maxLength !== undefined && value.length > policy.maxLength) return false;
  if (policy.pattern && !new RegExp(`^(?:${policy.pattern})$`).test(value)) return false;
  if (policy.noSeparators && (separator.test(value) || value === "." || value === ".."))
    return false;
  return true;
}

export function validReaderParameter(type: string, value: string): boolean {
  const policy = readerDestinationContract.parameters[type];
  return Boolean(
    policy &&
    !separator.test(value) &&
    validText(value, policy) &&
    (!policy.values || Object.hasOwn(policy.values, value)),
  );
}

/** Match raw path segments before decoding parameters; URL normalization never chooses a route. */
export function matchReaderDestinationPath(pathname: string): MatchedReaderDestination | null {
  if (
    !pathname.startsWith("/") ||
    pathname.startsWith("//") ||
    pathname.includes("?") ||
    pathname.includes("#") ||
    unsafeURL.test(pathname) ||
    invalidEscape.test(pathname) ||
    !pathname.isWellFormed()
  )
    return null;
  const path = pathname.length > 1 && pathname.endsWith("/") ? pathname.slice(0, -1) : pathname;
  const parts = path === "/" ? [] : path.slice(1).split("/");
  for (const definition of readerDestinationContract.routes) {
    const candidates = [
      { segments: definition.segments, parameters: {} },
      ...(definition.aliases ?? []),
    ];
    for (const candidate of candidates) {
      if (candidate.segments.length !== parts.length) continue;
      const parameters: Record<string, string> = { ...candidate.parameters };
      let matches = true;
      for (let index = 0; index < parts.length; index++) {
        const segment = candidate.segments[index];
        if (typeof segment === "string") {
          if (segment !== parts[index]) matches = false;
          continue;
        }
        let decoded: string;
        try {
          decoded = decodeURIComponent(parts[index]);
        } catch {
          matches = false;
          break;
        }
        const policy = readerDestinationContract.parameters[segment.type];
        const value = policy?.values
          ? Object.entries(policy.values).find(([, wire]) => wire === decoded)?.[0]
          : decoded;
        if (value === undefined || !validReaderParameter(segment.type, value)) {
          matches = false;
          break;
        }
        parameters[segment.name] = value;
      }
      if (matches)
        return { definition, destination: { routeId: definition.id, parameters, query: {} } };
    }
  }
  return null;
}

function validQueryValue(value: string, policy: QueryPolicy): boolean {
  if (!validText(value, policy)) return false;
  if (policy.values && !policy.values.includes(value)) return false;
  if (policy.format === "integer") {
    if (!/^\d+$/.test(value)) return false;
    const number = Number(value);
    if (!Number.isSafeInteger(number)) return false;
    if (policy.min !== undefined && number < policy.min) return false;
    if (policy.max !== undefined && number > policy.max) return false;
  }
  if (policy.format === "date") {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || value.startsWith("0000-")) return false;
    const date = new Date(`${value}T00:00:00Z`);
    if (!Number.isFinite(date.getTime()) || date.toISOString().slice(0, 10) !== value) return false;
  }
  return true;
}

export function validReaderQueryValue(
  field: keyof typeof contract.queryFields,
  value: string,
): boolean {
  const policy = readerDestinationContract.queryFields[field];
  return typeof value === "string" && Boolean(policy && validQueryValue(value, policy));
}

function validQuery(
  query: Record<string, string>,
  definition: ReaderDestinationDefinition,
): boolean {
  const profile = readerDestinationContract.queryProfiles[definition.query];
  if (!profile) return false;
  for (const [name, value] of Object.entries(query)) {
    const field = Object.hasOwn(profile.fields, name) ? profile.fields[name] : undefined;
    const policy = field && readerDestinationContract.queryFields[field];
    if (typeof value !== "string" || !policy || !validQueryValue(value, policy)) return false;
  }
  return !profile.required?.some((name) => !Object.hasOwn(query, name));
}

/** Decode untrusted return_to only at the authentication boundary. */
export function decodeReaderLoginDestination(href: string): ReaderDestinationRecord | null {
  if (
    typeof href !== "string" ||
    href.length > readerDestinationContract.maxLength ||
    href.includes("#") ||
    unsafeURL.test(href) ||
    invalidEscape.test(href) ||
    !href.isWellFormed()
  )
    return null;
  const separatorIndex = href.indexOf("?");
  const matched = matchReaderDestinationPath(
    separatorIndex < 0 ? href : href.slice(0, separatorIndex),
  );
  if (!matched?.definition.login) return null;
  const rawQuery = separatorIndex < 0 ? "" : href.slice(separatorIndex + 1);
  const query: Record<string, string> = {};
  if (rawQuery) {
    for (const pair of rawQuery.split("&")) {
      const equalIndex = pair.indexOf("=");
      if (equalIndex < 1) return null;
      let name: string;
      let value: string;
      try {
        name = decodeURIComponent(pair.slice(0, equalIndex).replace(/\+/g, " "));
        value = decodeURIComponent(pair.slice(equalIndex + 1).replace(/\+/g, " "));
      } catch {
        return null;
      }
      if (Object.hasOwn(query, name)) return null;
      Object.defineProperty(query, name, {
        value,
        enumerable: true,
        configurable: true,
        writable: true,
      });
    }
  }
  const destination = { ...matched.destination, query };
  return validQuery(query, matched.definition) && serializeReaderLoginDestination(destination)
    ? destination
    : null;
}

/** Validate again when serializing records received from storage or other runtimes. */
export function serializeReaderDestination(destination: ReaderDestinationRecord): string | null {
  if (
    !destination ||
    typeof destination !== "object" ||
    Array.isArray(destination) ||
    typeof destination.routeId !== "string" ||
    Object.keys(destination).some((key) => !["routeId", "parameters", "query"].includes(key)) ||
    !destination.parameters ||
    !destination.query ||
    typeof destination.parameters !== "object" ||
    typeof destination.query !== "object" ||
    Array.isArray(destination.parameters) ||
    Array.isArray(destination.query)
  )
    return null;
  const definition = readerDestinationContract.routes.find(
    (route) => route.id === destination.routeId,
  );
  if (!definition || !validQuery(destination.query, definition)) return null;
  const names = definition.segments.flatMap((segment) =>
    typeof segment === "string" ? [] : [segment.name],
  );
  if (Object.keys(destination.parameters).length !== names.length) return null;
  const segments: string[] = [];
  for (const segment of definition.segments) {
    if (typeof segment === "string") segments.push(segment);
    else {
      const value = destination.parameters[segment.name];
      if (typeof value !== "string" || !validReaderParameter(segment.type, value)) return null;
      segments.push(
        encodeURIComponent(
          readerDestinationContract.parameters[segment.type].values?.[value] ?? value,
        ),
      );
    }
  }
  const query = new URLSearchParams(destination.query).toString();
  const href = `/${segments.join("/")}${query ? `?${query}` : ""}`;
  return href.length <= readerDestinationContract.maxLength ? href : null;
}

/** Authentication requires an explicitly approved destination in addition to a valid route. */
export function serializeReaderLoginDestination(
  destination: ReaderDestinationRecord,
): string | null {
  if (
    !destination ||
    !readerDestinationContract.routes.some(
      (definition) => definition.id === destination.routeId && definition.login,
    )
  )
    return null;
  return serializeReaderDestination(destination);
}

/** Invalid UI links fall back to the reader home; direct API inputs remain strict. */
export function readerSignInDestination(value: string | ReaderDestinationRecord): string {
  const destination = typeof value === "string" ? decodeReaderLoginDestination(value) : value;
  return destination ? (serializeReaderLoginDestination(destination) ?? "/") : "/";
}
