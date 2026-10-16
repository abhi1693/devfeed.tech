export type PageRouteDescriptor = { path: string; label: string };

type RouteNode = {
  children: Map<string, RouteNode>;
  parameter?: RouteNode;
  label?: string;
  remainder?: string;
};
const compiled = new WeakMap<readonly PageRouteDescriptor[], RouteNode>();
const routeNode = (): RouteNode => ({ children: new Map() });

function routeTree(descriptors: readonly PageRouteDescriptor[]): RouteNode {
  const existing = compiled.get(descriptors);
  if (existing) return existing;
  const root = routeNode();
  for (const { path, label } of descriptors) {
    let node = root;
    const segments = path === "/" ? [] : path.slice(1).split("/");
    for (const segment of segments) {
      if (segment.startsWith(":*")) {
        node.remainder = label;
        break;
      }
      if (segment.startsWith(":")) node = node.parameter ??= routeNode();
      else {
        let child = node.children.get(segment);
        if (!child) {
          child = routeNode();
          node.children.set(segment, child);
        }
        node = child;
      }
    }
    if (!segments.at(-1)?.startsWith(":*")) node.label = label;
  }
  compiled.set(descriptors, root);
  return root;
}

function match(node: RouteNode, parts: readonly string[], index = 0): string | null {
  if (index === parts.length) return node.label ?? null;
  const exact = node.children.get(parts[index]);
  const label = exact ? match(exact, parts, index + 1) : null;
  return (
    label ??
    (node.parameter ? match(node.parameter, parts, index + 1) : null) ??
    node.remainder ??
    null
  );
}

/** Match route templates, never preserve a request parameter in a telemetry name. */
export function resolvePageRoute(
  pathname: string,
  descriptors: readonly PageRouteDescriptor[],
): string | null {
  if (!pathname.startsWith("/") || pathname.includes("?") || pathname.includes("#")) return null;
  let parts: string[];
  try {
    const path = pathname.length > 1 && pathname.endsWith("/") ? pathname.slice(0, -1) : pathname;
    parts = path === "/" ? [] : path.slice(1).split("/").map(decodeURIComponent);
    if (parts.some((part) => !part || /[/\\\u0000-\u001f\u007f]/u.test(part))) return null;
  } catch {
    return null;
  }
  return match(routeTree(descriptors), parts);
}
