// Keep this policy aligned with devfeed_core.log_privacy and the shared fixtures.
const redacted = "[REDACTED]";
const credentialFields = new Set([
  "accesstoken",
  "refreshtoken",
  "idtoken",
  "token",
  "clientsecret",
  "codeverifier",
  "assertion",
  "clientassertion",
  "password",
  "passwd",
  "secret",
  "apikey",
  "authorization",
  "cookie",
  "setcookie",
  "samlresponse",
  "oauthtoken",
  "oauthverifier",
  "devicecode",
  "usercode",
]);
const queryFields = new Set([...credentialFields, "code", "state", "sessionstate", "relaystate"]);
function decoded(value: string) {
  // Decode valid runs independently: malformed input must not hide an encoded key.
  return value.replace(/(?:%[a-f\d]{2})+/gi, (run) => {
    try {
      return decodeURIComponent(run);
    } catch {
      return run;
    }
  });
}
function name(value: string) {
  return decoded(value).toLowerCase().replace(/[_.-]/g, "");
}
function credentialField(value: string) {
  return credentialFields.has(name(value.split(".").at(-1) || value));
}
function encodedValue(value: string, depth: number): string {
  const plain = decoded(value);
  if (plain === value) return value;
  if (depth >= 5) return redacted;
  let sanitized = encodedValue(plain, depth + 1);
  if (sanitized.includes("?") || sanitized.includes("#") || sanitized.includes("://"))
    sanitized = redactAuthText(sanitized, depth + 1);
  return sanitized !== plain
    ? encodeURIComponent(sanitized).replace(
        /[!'()*]/g,
        (char) => `%${char.charCodeAt(0).toString(16).toUpperCase()}`,
      )
    : value;
}
export function redactAuthText(value: string, depth = 0): string {
  value = value.replace(/(\b(?:bearer|basic)\s+)[a-z\d._~+/-]+=*/gi, `$1${redacted}`);
  if (!value.includes("=") && !value.includes("@")) return value;
  value = value.replace(/(\b[a-z][a-z\d+.-]*:\/\/)[^/?#\s@]+@/gi, `$1${redacted}@`);
  return value.replace(
    /(^|[?&#;\s])([\w%+.-]+)=([^&#;\s"'<>]*)/g,
    (original, prefix: string, key: string, parameter: string) => {
      if (queryFields.has(name(key))) return `${prefix}${key}=${redacted}`;
      if (parameter.includes("%")) return `${prefix}${key}=${encodedValue(parameter, depth)}`;
      return original;
    },
  );
}
export function redactAuthentication(value: unknown, depth = 0): unknown {
  if (typeof value === "string") return redactAuthText(value);
  if (value === null || typeof value !== "object") return value;
  if (depth >= 20) return "[truncated]";
  if (Array.isArray(value)) return value.map((item) => redactAuthentication(item, depth + 1));
  const input = value as Record<string, unknown>;
  const output = Object.fromEntries(
    Object.entries(input).map(([key, item]) => [
      key,
      credentialField(key) ? redacted : redactAuthentication(item, depth + 1),
    ]),
  );
  // OTLP attributes store the semantic field name separately from its value.
  if (typeof input.key === "string" && "value" in input && credentialField(input.key))
    output.value =
      input.value !== null && typeof input.value === "object"
        ? { stringValue: redacted }
        : redacted;
  return output;
}
