import { readdir, readFile, writeFile } from "node:fs/promises";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";
import { format } from "prettier";
import { gatewayOperations } from "./lib/gateway-contract.mjs";

const root = new URL("../", import.meta.url);
const output = new URL("../packages/telemetry/src/page-routes.generated.ts", import.meta.url);

/** Read the page and route files that make endpoints public in the Next app router. */
export async function pageRouteTemplates(directory, segments = []) {
  const routes = [];
  const entries = await readdir(directory, { withFileTypes: true });
  for (const entry of entries) {
    if (entry.isFile() && /^(?:page|route)\.(?:js|jsx|ts|tsx)$/.test(entry.name))
      routes.push({
        route: `/${segments.join("/")}`,
        kind: entry.name.startsWith("page.") ? "page" : "endpoint",
      });
  }
  for (const entry of entries) {
    if (!entry.isDirectory() || entry.name.startsWith("_") || entry.name.startsWith("@")) continue;
    if (/^\(\.\.?\.?\)/.test(entry.name))
      throw new Error(`Unsupported intercepted route outside a parallel slot: ${entry.name}`);
    const group = entry.name.startsWith("(") && entry.name.endsWith(")");
    routes.push(
      ...(await pageRouteTemplates(
        new URL(`${entry.name}/`, directory),
        group ? segments : [...segments, entry.name],
      )),
    );
  }
  return routes.sort((left, right) => left.route.localeCompare(right.route));
}

async function exportsFrom(source) {
  const { outputFiles } = await build({
    stdin: { contents: source, resolveDir: fileURLToPath(root), loader: "ts" },
    bundle: true,
    write: false,
    platform: "node",
    format: "esm",
    logLevel: "silent",
  });
  return import(
    `data:text/javascript;base64,${Buffer.from(outputFiles[0].contents).toString("base64")}`
  );
}

function descriptorsFor({ route, label, parameters = {} }) {
  const pieces = route === "/" ? [] : route.slice(1).split("/");
  let paths = [[]];
  for (const piece of pieces) {
    const dynamic = piece.match(/^\[(.+)\]$/);
    let values = [piece];
    if (dynamic) {
      const name = dynamic[1];
      if (name.startsWith("["))
        throw new Error(`Optional catch-all must be expanded by its owner: ${route}`);
      values = parameters[name] ?? [name.startsWith("...") ? ":*path" : ":id"];
    }
    paths = paths.flatMap((path) => values.map((value) => [...path, value]));
  }
  return paths.map((pieces) => {
    const path = `/${pieces.join("/")}`;
    return {
      path,
      label: label ?? path.replaceAll(":*path", ":path").replace(/^\/api\/v1\//, "/v1/"),
    };
  });
}

function catalog(definitions) {
  const routes = new Map();
  for (const definition of definitions) {
    for (const descriptor of descriptorsFor(definition)) {
      const existing = routes.get(descriptor.path);
      if (existing && existing.label !== descriptor.label)
        throw new Error(`Conflicting label for ${descriptor.path}`);
      routes.set(descriptor.path, descriptor);
      if (descriptor.path.startsWith("/api/v1/")) {
        const alias = { ...descriptor, path: descriptor.path.slice("/api".length) };
        const previous = routes.get(alias.path);
        if (previous && previous.label !== alias.label)
          throw new Error(`Conflicting label for ${alias.path}`);
        routes.set(alias.path, alias);
      }
    }
  }
  const specificity = (path) =>
    path
      .split("/")
      .reduce(
        (score, part) => score + (part.startsWith(":*") ? -100 : part.startsWith(":") ? 1 : 10),
        0,
      );
  return [...routes.values()].sort(
    (left, right) =>
      specificity(right.path) - specificity(left.path) || left.path.localeCompare(right.path),
  );
}

export async function pageRouteCatalogs() {
  const [admin, partner, web] = await Promise.all([
    exportsFrom(
      `export { adminTelemetryDefinitions } from './apps/admin/src/lib/routes.ts'; export { partnerAccountSections, accountsPath } from './apps/admin/src/lib/partner-account-routes.ts'; export { partnerDetailSections } from './apps/admin/src/lib/partner-detail-routes.ts'; export { settingsSections } from './apps/admin/src/lib/settings.ts';`,
    ),
    exportsFrom(`export { accountSettingsSections } from './apps/partner/src/lib/routes.ts';`),
    exportsFrom(
      `export { readerTelemetryDefinitions } from './apps/web/src/lib/reader-routes.ts';`,
    ),
  ]);
  const result = {};
  for (const app of ["web", "admin", "partner"]) {
    const nextRoutes = await pageRouteTemplates(
      new URL(`../apps/${app}/src/app/`, import.meta.url),
    );
    const definitions = [
      { route: "/_next/static/[...asset]", label: "/_next/static/:asset" },
      { route: "/_next/image" },
    ];
    for (const item of nextRoutes) {
      if (app === "admin" && item.route === "/[...segments]") {
        definitions.push(...admin.adminTelemetryDefinitions());
      } else if (app === "admin" && item.route === "/api/[...path]") {
        const document = JSON.parse(
          await readFile(new URL("../apps/admin/openapi.json", import.meta.url), "utf8"),
        );
        definitions.push(
          ...gatewayOperations(document, { namespace: "admin" }).map(({ path, parameters }) => ({
            route: `/api${path.replace(/\{([^}]+)\}/g, (_, name) => (parameters.find((parameter) => parameter.name === name)?.converter === "path" ? `[...${name}]` : `[${name}]`))}`,
          })),
        );
      } else if (app === "admin" && item.route === `${admin.accountsPath}/[id]/[[...section]]`) {
        definitions.push(
          ...admin.partnerAccountSections.map(({ segments }) => ({
            route: `${admin.accountsPath}/[id]${segments.length ? `/${segments.map((segment) => (segment === ":identifier" ? "[identifier]" : segment)).join("/")}` : ""}`,
          })),
        );
      } else if (app === "admin" && item.route.endsWith("/[[...section]]")) {
        const resource = item.route.split("/")[2];
        const sections = admin.partnerDetailSections[resource];
        if (!sections) throw new Error(`Unknown optional section owner: ${item.route}`);
        const base = item.route.slice(0, -"/[[...section]]".length);
        definitions.push(
          { route: base },
          ...sections.map((section) => ({ route: `${base}/${section}` })),
        );
      } else if (app === "admin" && item.route === "/settings/[section]") {
        definitions.push({
          route: item.route,
          parameters: { section: admin.settingsSections.map(({ id }) => id) },
        });
      } else if (app === "partner" && item.route === "/settings/[accountId]/[settingsSection]") {
        definitions.push({
          route: item.route,
          parameters: { settingsSection: partner.accountSettingsSections },
        });
      } else if (
        app === "web" &&
        web.readerTelemetryDefinitions.some(({ route }) => route === item.route)
      ) {
        definitions.push(
          ...web.readerTelemetryDefinitions.filter(({ route }) => route === item.route),
        );
      } else {
        if (item.route.includes("[contentType]"))
          throw new Error(`Content route has no reader descriptor: ${item.route}`);
        definitions.push(item);
      }
    }
    result[app] = catalog(definitions);
  }
  return result;
}

export async function renderPageRoutes() {
  return format(
    `// Generated from registered Next routes and their shared descriptors.\n// Run node scripts/generate_page_routes.mjs; do not edit.\nimport type { PageRouteDescriptor } from './page-routes';\nexport const pageRouteCatalogs = ${JSON.stringify(await pageRouteCatalogs())} as const satisfies Record<'web' | 'admin' | 'partner', readonly PageRouteDescriptor[]>;\n`,
    { parser: "typescript", printWidth: 100 },
  );
}

export async function generatePageRoutes({ check = false } = {}) {
  const rendered = await renderPageRoutes();
  if (check) {
    if ((await readFile(output, "utf8").catch(() => "")) !== rendered)
      throw new Error("Page descriptors are stale. Run node scripts/generate_page_routes.mjs.");
  } else await writeFile(output, rendered);
}

if (process.argv[1] && pathToFileURL(process.argv[1]).href === import.meta.url) {
  const arguments_ = process.argv.slice(2);
  if (arguments_.length > 1 || (arguments_.length === 1 && arguments_[0] !== "--check"))
    throw new Error("Usage: node scripts/generate_page_routes.mjs [--check]");
  await generatePageRoutes({ check: arguments_.includes("--check") });
  console.log(`Next route descriptors ${arguments_.length ? "checked" : "generated"}`);
}
