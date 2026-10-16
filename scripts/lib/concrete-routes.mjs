import { mkdir, readFile, readdir, unlink } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { format } from "prettier";
import { gatewayOperations, releaseVersion } from "./gateway-contract.mjs";
import { readRegularFile, writeGeneratedFile } from "./generated-files.mjs";

export function createRouteGenerator(config) {
  const marker = config.marker;
  function header(version) {
    return `/**\n${marker}\n * OpenAPI spec version: ${version}\n */\n`;
  }

  function nextSegments(operation) {
    return operation.path
      .slice(`/v1/${config.namespace}/`.length)
      .split("/")
      .map((part) => {
        const parameter = operation.parameters.find(({ name }) => part === `{${name}}`);
        if (parameter) {
          return {
            filename:
              parameter.converter === "path" ? `[...${parameter.name}]` : `[${parameter.name}]`,
            parameter,
          };
        }
        if (!/^[A-Za-z0-9_.:-]+$/u.test(part)) {
          throw new Error(`Unsupported Next static route segment ${part}`);
        }
        return { filename: part };
      });
  }

  function validateFilenames(filenames) {
    const trie = { children: new Map() };
    for (const filename of filenames) {
      let node = trie;
      for (const part of filename.split("/").slice(0, -1)) {
        const key = part.startsWith("[") ? "dynamic" : part;
        let child = node.children.get(key);
        if (!child) {
          child = { filename: part, children: new Map() };
          node.children.set(key, child);
        } else if (child.filename !== part) {
          throw new Error(`Conflicting Next dynamic route names ${child.filename} and ${part}`);
        }
        node = child;
      }
    }
  }

  /** Concrete routes have one consistent set of Next parameter names at each depth. */
  function routePlan(document) {
    const operations = gatewayOperations(document, { namespace: config.namespace });
    const groups = new Map();
    const ids = new Set();
    for (const operation of operations) {
      if (ids.has(operation.operationId)) {
        throw new Error(`Duplicate ${config.name} operation identity ${operation.operationId}`);
      }
      ids.add(operation.operationId);
      const segments = nextSegments(operation);
      const filename = segments.map(({ filename }) => filename).join("/") + "/route.ts";
      let group = groups.get(filename);
      if (!group) {
        group = { filename, path: operation.path, operations: [] };
        groups.set(filename, group);
      }
      if (group.path !== operation.path) {
        throw new Error(`Inconsistent backend path template for ${filename}`);
      }
      if (group.operations.some(({ method }) => method === operation.method)) {
        throw new Error(`Duplicate concrete ${operation.method} operation for ${filename}`);
      }
      group.operations.push(operation);
    }
    validateFilenames(groups.keys());
    return { operations, groups: [...groups.values()] };
  }

  async function renderRoutes(document) {
    const version = releaseVersion(document);
    const { operations, groups } = routePlan(document);
    const definitions = operations
      .map(
        (operation) => `[${JSON.stringify(operation.operationId)}]: ${JSON.stringify(operation)}`,
      )
      .join(",\n");
    const operationSource = await format(
      header(version) +
        `import type { ${config.operationType} } from "@devfeed/http/gateway";\n\n` +
        `export const ${config.operationExport} = {\n${definitions}\n} as const satisfies Readonly<Record<string, ${config.operationType}>>;\n`,
      { parser: "typescript", printWidth: 100 },
    );
    const routes = new Map(
      await Promise.all(
        groups.map(async ({ filename, operations: methods }) => {
          const allowed = methods.map(({ method }) => method);
          const rejectHead = allowed.includes("GET") && !allowed.includes("HEAD");
          const imports = new Map();
          const useBinding = ({ name, module }) => {
            if (!imports.has(module)) imports.set(module, new Set());
            imports.get(module).add(name);
            return name;
          };
          const bindings = methods
            .map((operation) => {
              const selected = config.bindings[operation.policy.handler ?? "forward"];
              if (!selected)
                throw new Error(`Unsupported generated handler for ${operation.operationId}`);
              const bind = useBinding(selected);
              return `export const ${operation.method} = ${bind}(${config.operationExport}[${JSON.stringify(operation.operationId)}]);`;
            })
            .join("\n");
          if (rejectHead) useBinding(config.reject);
          const importSource = [...imports]
            .map(
              ([module, names]) =>
                `import { ${[...names].join(", ")} } from ${JSON.stringify(module)};\n`,
            )
            .join("");
          const source = await format(
            header(version) +
              importSource +
              `import { ${config.operationExport} } from ${JSON.stringify(config.operationsImport)};\n\n` +
              `export const dynamic = "force-dynamic";\n` +
              `export const runtime = "nodejs";\n\n` +
              bindings +
              (rejectHead
                ? `\nexport const HEAD = ${config.reject.name}(${JSON.stringify(allowed)});\n`
                : "\n"),
            { parser: "typescript", printWidth: 100 },
          );
          return [filename, source];
        }),
      ),
    );
    return { operations: operationSource, routes };
  }

  async function existingRoutes(root, relative = "", files = new Map()) {
    let entries;
    try {
      entries = await readdir(join(root, relative), { withFileTypes: true });
    } catch (error) {
      if (error.code === "ENOENT") return files;
      throw error;
    }
    for (const entry of entries) {
      const path = relative ? `${relative}/${entry.name}` : entry.name;
      if (entry.isSymbolicLink())
        throw new Error(`Cannot manage symlinked ${config.name} route ${path}`);
      if (entry.isDirectory()) await existingRoutes(root, path, files);
      else if (entry.isFile() && entry.name === "route.ts") {
        files.set(path, await readRegularFile(join(root, path), `${config.name} route ${path}`));
      }
    }
    return files;
  }

  async function previousOperationSource(target) {
    try {
      return await readRegularFile(target, `${config.name} operation definition`);
    } catch (error) {
      if (error.code === "ENOENT") return undefined;
      throw error;
    }
  }

  /** Only generated files carrying our marker may be overwritten or removed. */
  async function generateRoutes({
    check = false,
    source = config.source,
    target = config.target,
    routesRoot = config.routesRoot,
  } = {}) {
    const root = routesRoot instanceof URL ? fileURLToPath(routesRoot) : routesRoot;
    const generated = await renderRoutes(JSON.parse(await readFile(source, "utf8")));
    const existing = await existingRoutes(root);
    validateFilenames([
      ...generated.routes.keys(),
      ...[...existing]
        .filter(([, content]) => !content.includes(marker))
        .map(([filename]) => filename),
    ]);
    const previous = await previousOperationSource(target);
    if (previous !== undefined && !previous.includes(marker)) {
      throw new Error(`Refusing to overwrite a handwritten ${config.name} operation definition`);
    }
    for (const filename of generated.routes.keys()) {
      if (existing.has(filename) && !existing.get(filename).includes(marker)) {
        throw new Error(`Refusing to overwrite handwritten ${config.name} route ${filename}`);
      }
    }
    const obsolete = [...existing]
      .filter(([filename, content]) => content.includes(marker) && !generated.routes.has(filename))
      .map(([filename]) => filename);
    if (check) {
      if (
        previous !== generated.operations ||
        obsolete.length ||
        [...generated.routes].some(([filename, content]) => existing.get(filename) !== content)
      ) {
        throw new Error(
          `${config.label} routes are stale; run npm run ${config.generateCommand ?? `${config.name}:generate`}`,
        );
      }
      return;
    }
    const targetPath = target instanceof URL ? fileURLToPath(target) : target;
    await mkdir(dirname(targetPath), { recursive: true });
    await writeGeneratedFile(targetPath, generated.operations, {
      marker,
      label: `${config.name} operation definition`,
    });
    for (const [filename, content] of generated.routes) {
      const destination = join(root, filename);
      await mkdir(dirname(destination), { recursive: true });
      await writeGeneratedFile(destination, content, {
        marker,
        label: `${config.name} route ${filename}`,
      });
    }
    for (const filename of obsolete) await unlink(join(root, filename));
  }

  return { plan: routePlan, render: renderRoutes, generate: generateRoutes };
}
