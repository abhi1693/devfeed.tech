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
type CompiledOperation = { operation: GatewayOperation; parts: RoutePart[] };

// Starlette's UUID converter accepts canonical and compact UUIDs.
const uuid = /^[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}$/;
const unsafeSegment = /[/\\?#%\u0000-\u0020\u007f]/u;

export class GatewayRouter {
  private readonly routes: CompiledOperation[];

  constructor({ operations }: { operations: readonly GatewayOperation[] }) {
    this.routes = operations.map((operation) => ({
      operation,
      parts: operation.path
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
        }),
    }));
  }

  resolve(method: string, segments: readonly string[]): GatewayMatch {
    if (
      !segments.length ||
      segments.some(
        (part) =>
          !part ||
          !part.isWellFormed() ||
          part === "." ||
          part === ".." ||
          unsafeSegment.test(part),
      )
    )
      return { kind: "not-found" };
    const allowedMethods = new Set<string>();
    for (const { operation, parts } of this.routes) {
      const parameters = this.match(parts, segments);
      if (parameters === null) continue;
      allowedMethods.add(operation.method);
      if (operation.method !== method) continue;
      if (!this.validate(parts, parameters)) return { kind: "invalid-parameters" };
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

  private match(parts: RoutePart[], segments: readonly string[]): Record<string, string> | null {
    const parameters: Record<string, string> = Object.create(null);
    let index = 0;
    for (const part of parts) {
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

  private validate(parts: RoutePart[], parameters: Record<string, string>): boolean {
    return parts.every((part) => {
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
