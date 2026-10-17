const methods = new Set(["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]);
const converters = new Set(["str", "uuid", "path"]);
const policyFields = ["bodyLimit", "bodyTimeoutMs", "upstreamTimeoutMs"];
const schemaFields = new Set(["type", "format", "enum", "minLength", "maxLength", "pattern"]);
const annotations = new Set(["title", "description", "examples", "example"]);
function object(value, label) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${label} must be an object`);
  }
  return value;
}

function keys(value, accepted, label) {
  for (const key of Object.keys(value)) {
    if (!accepted.has(key)) throw new Error(`${label} has unsupported field ${key}`);
  }
}

export function extensionTransportPolicy(value, label = "Extension policy") {
  object(value, label);
  keys(value, new Set(["credentials", "cacheControl", "requestHeaders", "timeoutMs"]), label);
  if (!["include", "omit"].includes(value.credentials)) {
    throw new Error(`${label}.credentials must declare include or omit`);
  }
  if (!["public-read", "no-store", null].includes(value.cacheControl)) {
    throw new Error(`${label}.cacheControl must declare a supported cache policy`);
  }
  const headers = new Set([
    "Content-Type",
    "X-CSRF-Token",
    "If-None-Match",
    "X-Devfeed-Reader-Measurement",
  ]);
  if (
    !Array.isArray(value.requestHeaders) ||
    value.requestHeaders.some((header) => !headers.has(header)) ||
    new Set(value.requestHeaders).size !== value.requestHeaders.length
  ) {
    throw new Error(`${label}.requestHeaders must declare distinct supported headers`);
  }
  if (!Number.isSafeInteger(value.timeoutMs) || value.timeoutMs <= 0) {
    throw new Error(`${label}.timeoutMs must be a positive safe integer`);
  }
  return {
    credentials: value.credentials,
    cacheControl: value.cacheControl,
    requestHeaders: [...value.requestHeaders],
    timeoutMs: value.timeoutMs,
  };
}

export function gatewayPolicy(value, label, namespace) {
  object(value, label);
  const reader = namespace === "user";
  keys(
    value,
    new Set([...policyFields, ...(reader ? ["cookiePolicy", "handler", "extension"] : [])]),
    label,
  );
  const result = {};
  for (const field of policyFields) {
    if (!Number.isSafeInteger(value[field]) || value[field] <= 0) {
      throw new Error(`${label}.${field} must be a positive safe integer`);
    }
    result[field] = value[field];
  }
  if (reader) {
    if (!["reader", "reader-attribution", "omit"].includes(value.cookiePolicy)) {
      throw new Error(`${label}.cookiePolicy must declare a supported reader cookie policy`);
    }
    if (!["forward", "placement-query"].includes(value.handler)) {
      throw new Error(`${label}.handler must declare a supported reader handler`);
    }
    result.cookiePolicy = value.cookiePolicy;
    result.handler = value.handler;
    if (!("extension" in value)) {
      throw new Error(`${label}.extension must explicitly declare eligibility or null`);
    }
    result.extension =
      value.extension === null
        ? null
        : extensionTransportPolicy(value.extension, `${label}.extension`);
  }
  return result;
}

function resolveSchema(document, value, label, refs = new Set()) {
  object(value, label);
  if (!("$ref" in value)) return value;
  keys(value, new Set(["$ref", ...annotations]), label);
  const reference = value.$ref;
  if (typeof reference !== "string" || !reference.startsWith("#/components/schemas/")) {
    throw new Error(`${label} uses an unsupported schema reference`);
  }
  if (refs.has(reference)) throw new Error(`${label} has a cyclic schema reference`);
  const name = reference.slice("#/components/schemas/".length);
  if (!name || name.includes("/") || name.includes("~")) {
    throw new Error(`${label} uses an unsupported schema reference`);
  }
  const next = new Set(refs).add(reference);
  return resolveSchema(document, document.components?.schemas?.[name], label, next);
}

export function gatewayParameterSchema(document, value, label) {
  const resolved = resolveSchema(document, value, label);
  keys(resolved, new Set([...schemaFields, ...annotations]), label);
  if (resolved.type !== "string") throw new Error(`${label} must be a string schema`);
  const result = { type: "string" };
  if ("format" in resolved) {
    if (resolved.format !== "uuid") throw new Error(`${label} uses an unsupported format`);
    result.format = resolved.format;
  }
  if ("enum" in resolved) {
    if (
      !Array.isArray(resolved.enum) ||
      !resolved.enum.length ||
      resolved.enum.some((item) => typeof item !== "string") ||
      new Set(resolved.enum).size !== resolved.enum.length
    ) {
      throw new Error(`${label} must have distinct string enum values`);
    }
    result.enum = [...resolved.enum];
  }
  for (const field of ["minLength", "maxLength"]) {
    if (field in resolved) {
      if (!Number.isSafeInteger(resolved[field]) || resolved[field] < 0) {
        throw new Error(`${label}.${field} must be a nonnegative safe integer`);
      }
      result[field] = resolved[field];
    }
  }
  if ((result.minLength ?? 0) > (result.maxLength ?? Infinity)) {
    throw new Error(`${label} has contradictory string bounds`);
  }
  if ("pattern" in resolved) {
    if (typeof resolved.pattern !== "string") throw new Error(`${label}.pattern must be a string`);
    try {
      new RegExp(resolved.pattern, "u");
    } catch {
      throw new Error(`${label} uses a pattern unsupported by JavaScript`);
    }
    result.pattern = resolved.pattern;
  }
  return result;
}

function template(path, label, namespace) {
  if (
    typeof path !== "string" ||
    !path.startsWith(`/v1/${namespace}/`) ||
    /[\s\\%?#\x00-\x1f\x7f]/u.test(path)
  ) {
    throw new Error(`${label} must be an absolute ${namespace} route template`);
  }
  const parts = path.slice(1).split("/");
  const names = [];
  for (const part of parts) {
    if (!part || part === "." || part === "..") throw new Error(`${label} has unsafe segments`);
    const parameter = /^\{([A-Za-z_][A-Za-z0-9_]*)\}$/u.exec(part);
    if (parameter) {
      if (names.includes(parameter[1])) throw new Error(`${label} repeats a path parameter`);
      names.push(parameter[1]);
    } else if (/[{}]/u.test(part)) {
      throw new Error(`${label} has an unsupported route template`);
    }
  }
  return { names, parts };
}

/** Compare validated route templates without depending on their parameter names. */
export function gatewayRouteShape(path) {
  return path
    .split("/")
    .map((segment) => (segment.startsWith("{") ? "{}" : segment))
    .join("/");
}

/** Validate the ordered backend export without inferring endpoint policy from URLs. */
export function gatewayOperations(document, { namespace }) {
  if (typeof namespace !== "string" || !/^[a-z][a-z0-9-]*$/u.test(namespace)) {
    throw new Error("Gateway namespace must be a service name");
  }
  object(document, "OpenAPI document");
  const routes = document["x-devfeed-gateway-routes"];
  if (!Array.isArray(routes) || !routes.length) throw new Error("Missing ordered gateway routes");
  const signatures = new Set();
  const ids = new Map();
  const operations = routes.map((route, index) => {
    const label = `Gateway route ${index}`;
    object(route, label);
    keys(route, new Set(["operationId", "method", "path", "parameters", "policy"]), label);
    if (typeof route.operationId !== "string" || !route.operationId.length) {
      throw new Error(`${label} needs an operationId`);
    }
    if (!methods.has(route.method)) throw new Error(`${label} uses an unsupported method`);
    const { names, parts } = template(route.path, label, namespace);
    if (!Array.isArray(route.parameters) || route.parameters.length !== names.length) {
      throw new Error(`${label} has incomplete path parameters`);
    }
    const byName = new Map();
    for (const parameter of route.parameters) {
      object(parameter, `${label} parameter`);
      keys(parameter, new Set(["name", "schema", "converter"]), `${label} parameter`);
      if (!names.includes(parameter.name) || byName.has(parameter.name)) {
        throw new Error(`${label} has an unknown or repeated path parameter`);
      }
      if (!converters.has(parameter.converter)) {
        throw new Error(`${label} uses an unsupported path converter`);
      }
      if (parameter.converter === "path" && parts.at(-1) !== `{${parameter.name}}`) {
        throw new Error(`${label} needs a terminal catch-all parameter`);
      }
      const normalized = gatewayParameterSchema(
        document,
        parameter.schema,
        `${label} parameter ${parameter.name}`,
      );
      if (parameter.converter === "uuid" && normalized.format !== "uuid") {
        throw new Error(`${label} UUID converter needs a UUID schema`);
      }
      byName.set(parameter.name, {
        name: parameter.name,
        schema: normalized,
        converter: parameter.converter,
      });
    }
    const result = {
      operationId: route.operationId,
      method: route.method,
      path: route.path,
      parameters: names.map((name) => byName.get(name)),
      policy: gatewayPolicy(route.policy, `${label} policy`, namespace),
    };
    if (result.policy.handler === "placement-query" && result.method !== "POST") {
      throw new Error(`${label} placement-query handler requires POST`);
    }
    const signature = `${route.method} ${gatewayRouteShape(route.path)} ${result.parameters.map((item) => item.converter).join(",")}`;
    if (signatures.has(signature)) throw new Error(`${label} repeats a method/route signature`);
    signatures.add(signature);
    const identity = JSON.stringify({ ...result, method: undefined });
    if (ids.has(route.operationId) && ids.get(route.operationId) !== identity) {
      throw new Error(`${label} reuses an operationId for another operation`);
    }
    ids.set(route.operationId, identity);
    return result;
  });
  const byRoute = new Map(operations.map((item) => [`${item.method} ${item.path}`, item]));
  for (const [path, pathItem] of Object.entries(object(document.paths, "OpenAPI paths"))) {
    if (!path.startsWith(`/v1/${namespace}/`)) continue;
    for (const [method, operation] of Object.entries(object(pathItem, path))) {
      if (!methods.has(method.toUpperCase())) continue;
      object(operation, `${method} ${path}`);
      const exported = byRoute.get(`${method.toUpperCase()} ${path}`);
      if (!exported || exported.operationId !== operation.operationId) {
        throw new Error(`Missing matching gateway export for ${method.toUpperCase()} ${path}`);
      }
      const declared = gatewayPolicy(
        operation["x-devfeed-gateway"],
        `${method} ${path} policy`,
        namespace,
      );
      if (JSON.stringify(exported.policy) !== JSON.stringify(declared)) {
        throw new Error(`Gateway policy differs for ${method.toUpperCase()} ${path}`);
      }
      const declaredParameters = [...(pathItem.parameters ?? []), ...(operation.parameters ?? [])]
        .filter((parameter) => parameter.in === "path")
        .map((parameter) => ({
          name: parameter.name,
          schema: gatewayParameterSchema(document, parameter.schema, `${method} ${path} parameter`),
        }));
      if (
        declaredParameters.length !== exported.parameters.length ||
        exported.parameters.some(
          (parameter) =>
            !declaredParameters.some(
              (declaredParameter) =>
                declaredParameter.name === parameter.name &&
                JSON.stringify(declaredParameter.schema) === JSON.stringify(parameter.schema),
            ),
        )
      ) {
        throw new Error(`Gateway parameters differ for ${method.toUpperCase()} ${path}`);
      }
    }
  }
  return operations;
}

export function releaseVersion(document) {
  object(document, "OpenAPI document");
  const version = object(document.info, "OpenAPI info").version;
  if (
    typeof version !== "string" ||
    version.trim() !== version ||
    !/^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$/u.test(version)
  ) {
    throw new Error("OpenAPI info.version must be a release version MAJOR.MINOR.PATCH");
  }
  return version;
}
