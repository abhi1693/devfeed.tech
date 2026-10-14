import { readFile, writeFile } from "node:fs/promises";
import { fileURLToPath, pathToFileURL } from "node:url";
import { format } from "prettier";

import { gatewayOperations, releaseVersion } from "./lib/gateway-contract.mjs";

const input = new URL("../apps/admin/openapi.json", import.meta.url);
const output = new URL(
  "../apps/admin/src/lib/api/generated/gateway-operations.ts",
  import.meta.url,
);

export function adminGatewayOperations(document) {
  return gatewayOperations(document, { namespace: "admin" });
}

export async function renderAdminGateway(document) {
  const version = releaseVersion(document);
  return format(
    `/**\n` +
      ` * Generated from the backend OpenAPI gateway contract.\n` +
      ` * Do not edit manually.\n` +
      ` * OpenAPI spec version: ${version}\n` +
      ` */\n` +
      `import type { GatewayOperation } from "@devfeed/http/gateway";\n\n` +
      `export const adminGatewayOperations = ${JSON.stringify(adminGatewayOperations(document), null, 2)} as const satisfies readonly GatewayOperation[];\n`,
    { parser: "typescript", printWidth: 100 },
  );
}

export async function generateAdminGateway({
  check = false,
  source = input,
  target = output,
} = {}) {
  const generated = await renderAdminGateway(JSON.parse(await readFile(source, "utf8")));
  if (check) {
    if ((await readFile(target, "utf8")) !== generated) {
      throw new Error("Admin gateway operations are stale; run npm run admin:generate");
    }
  } else {
    await writeFile(target, generated);
  }
}

if (process.argv[1] && pathToFileURL(process.argv[1]).href === import.meta.url) {
  const arguments_ = process.argv.slice(2);
  if (arguments_.length > 1 || (arguments_.length === 1 && arguments_[0] !== "--check")) {
    throw new Error("Usage: node scripts/generate_admin_gateway.mjs [--check]");
  }
  await generateAdminGateway({ check: arguments_.includes("--check") });
  console.log(
    `Admin gateway operations ${arguments_.length ? "checked" : "generated"}: ${fileURLToPath(output)}`,
  );
}
