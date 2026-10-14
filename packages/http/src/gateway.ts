/** Match exported API operations once; transport policy belongs to the operation. */
export type GatewayPolicy = {
  bodyLimit: number;
  bodyTimeoutMs: number;
  upstreamTimeoutMs: number;
};

export type GatewayParameter = {
  name: string;
  converter: "str" | "uuid" | "path";
  schema: {
    type: "string";
    format?: "uuid";
    enum?: readonly string[];
    minLength?: number;
    maxLength?: number;
    pattern?: string;
  };
};

export type GatewayOperation = {
  operationId: string;
  method: string;
  path: string;
  parameters: readonly GatewayParameter[];
  policy: GatewayPolicy;
};

export type GatewayCookiePolicy = "reader" | "reader-attribution" | "omit";

export type ReaderGatewayOperation = GatewayOperation & {
  policy: GatewayPolicy & {
    cookiePolicy: GatewayCookiePolicy;
    handler: "forward" | "placement-query";
  };
};

export type GatewayMatch =
  | {
      kind: "matched";
      operation: GatewayOperation;
      path: string;
      parameters: Record<string, string>;
    }
  | { kind: "not-found" | "invalid-parameters" }
  | { kind: "method-not-allowed"; allowedMethods: readonly string[] };

type RoutePart = string | { parameter: GatewayParameter; pattern?: RegExp };
export type GatewayParameters = Record<string, string | string[]>;

// Starlette's UUID converter accepts canonical and compact UUIDs.
const uuid = /^[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}$/;
const unsafeSegment = /[/\\?#%\u0000-\u0020\u007f]/u;

function safeSegment(part: string): boolean {
  return Boolean(
    part && part.isWellFormed() && part !== "." && part !== ".." && !unsafeSegment.test(part),
  );
}

/** A declared operation's path, usable after a framework has already selected its handler. */
export class GatewayRoute {
  private readonly parts: RoutePart[];

  constructor(readonly operation: GatewayOperation) {
    this.parts = operation.path
      .slice(1)
      .split("/")
      .map((part) => {
        const parameter = operation.parameters.find((value) => part === `{${value.name}}`);
        return parameter
          ? {
              parameter,
              ...(parameter.schema.pattern && {
                pattern: new RegExp(parameter.schema.pattern, "u"),
              }),
            }
          : part;
      });
  }

  bind(parameters: GatewayParameters): string | null {
    if (Object.keys(parameters).length !== this.operation.parameters.length) return null;
    const segments: string[] = [];
    for (const part of this.parts) {
      if (typeof part === "string") {
        segments.push(part);
        continue;
      }
      const value = Object.hasOwn(parameters, part.parameter.name)
        ? parameters[part.parameter.name]
        : undefined;
      if (part.parameter.converter === "path") {
        if (!Array.isArray(value) || !value.length || !value.every(safeSegment)) return null;
        segments.push(...value);
      } else {
        if (typeof value !== "string" || !safeSegment(value)) return null;
        segments.push(value);
      }
    }
    const matched = this.match(segments);
    return matched && this.validate(matched)
      ? "/" + segments.map(encodeURIComponent).join("/")
      : null;
  }

  match(segments: readonly string[]): Record<string, string> | null {
    const parameters: Record<string, string> = Object.create(null);
    let index = 0;
    for (const part of this.parts) {
      const value = segments[index];
      if (value === undefined) return null;
      if (typeof part === "string") {
        if (part !== value) return null;
      } else if (part.parameter.converter === "path") {
        parameters[part.parameter.name] = segments.slice(index).join("/");
        index = segments.length;
        break;
      } else {
        if (part.parameter.converter === "uuid" && !uuid.test(value)) return null;
        parameters[part.parameter.name] = value;
      }
      index += 1;
    }
    return index === segments.length ? parameters : null;
  }

  validate(parameters: Record<string, string>): boolean {
    return this.parts.every((part) => {
      if (typeof part === "string") return true;
      const { parameter, pattern } = part;
      const { schema } = parameter;
      const value = parameters[parameter.name];
      const length = [...value].length;
      return (
        (schema.minLength === undefined || length >= schema.minLength) &&
        (schema.maxLength === undefined || length <= schema.maxLength) &&
        (schema.enum === undefined || schema.enum.includes(value)) &&
        (schema.format !== "uuid" || uuid.test(value)) &&
        (pattern === undefined || pattern.test(value))
      );
    });
  }
}

export class GatewayRouter {
  private readonly routes: GatewayRoute[];

  constructor({ operations }: { operations: readonly GatewayOperation[] }) {
    this.routes = operations.map((operation) => new GatewayRoute(operation));
  }

  resolve(method: string, segments: readonly string[]): GatewayMatch {
    if (!segments.length || !segments.every(safeSegment)) return { kind: "not-found" };
    const allowedMethods = new Set<string>();
    for (const route of this.routes) {
      const { operation } = route;
      const parameters = route.match(segments);
      if (parameters === null) continue;
      allowedMethods.add(operation.method);
      if (operation.method !== method) continue;
      if (!route.validate(parameters)) return { kind: "invalid-parameters" };
      return {
        kind: "matched",
        operation,
        parameters,
        path: "/" + segments.map(encodeURIComponent).join("/"),
      };
    }
    return allowedMethods.size
      ? { kind: "method-not-allowed", allowedMethods: [...allowedMethods] }
      : { kind: "not-found" };
  }
}
