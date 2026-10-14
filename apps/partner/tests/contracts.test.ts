import { execFile } from "node:child_process";
import { cp, mkdir, mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { promisify } from "node:util";
import { describe, expect, it } from "vitest";
import { generatePartnerRoutes } from "../../../scripts/generate_partner_routes.mjs";
import {
  parseAccount,
  parseAudienceTopics,
  parseCreativePreview,
  parseDestinationPreview,
} from "../src/lib/contracts";
import { partnerAccount } from "./partner-fixtures";

const execute = promisify(execFile);
const root = fileURLToPath(new URL("../../../", import.meta.url));

describe("partner response contracts", () => {
  it("accepts the full serialized account and preserves its saved preferences", () => {
    const account = partnerAccount({
      outbound_destination: "listing_url",
      audience: {
        experience_levels: ["senior"],
        language_topic_ids: ["22222222-2222-4222-8222-222222222222"],
        tool_topic_ids: [],
      },
      ad_formats: ["feed", "engagement"],
    });
    expect(parseAccount(account, account.id)).toEqual(account);
  });

  it("rejects another account's response", () => {
    expect(() => parseAccount(partnerAccount(), "22222222-2222-4222-8222-222222222222")).toThrow();
  });

  it.each(["outbound_destination", "ad_formats", "audience", "allowed_promotion_kinds"])(
    "requires the serialized %s field instead of fabricating a default",
    (field) => {
      const account: Record<string, unknown> = { ...partnerAccount() };
      delete account[field];
      expect(() => parseAccount(account, partnerAccount().id)).toThrow();
    },
  );

  it("requires every serialized audience array", () => {
    const account = partnerAccount();
    expect(() =>
      parseAccount({ ...account, audience: { experience_levels: [] } }, account.id),
    ).toThrow();
  });

  it.each([["sponsored_content"], ["feed", "feed"]])(
    "rejects invalid automatic advertising formats: %j",
    (...adFormats) => {
      const account = partnerAccount();
      expect(() => parseAccount({ ...account, ad_formats: adFormats }, account.id)).toThrow();
    },
  );

  it("validates nullable preview envelopes and complete creative fields", () => {
    const product = {
      id: "22222222-2222-4222-8222-222222222222",
      name: "Shipyard",
      product_url: "https://shipyard.example",
      listing_url: null,
    };
    expect(parseDestinationPreview({ product })).toEqual(product);
    expect(parseDestinationPreview({ product: null })).toBeNull();
    const creative = {
      ...product,
      description: "Deploy services",
      image_url: null,
      logo_url: null,
      ad_image_status: "default",
    };
    expect(parseCreativePreview({ product: creative })).toEqual(creative);
    expect(parseCreativePreview({ product: null })).toBeNull();
    expect(() => parseDestinationPreview({})).toThrow();
    expect(() => parseCreativePreview({ product })).toThrow();
  });

  it("validates topic identifiers and complete pagination metadata", () => {
    const page = {
      items: [
        {
          id: "22222222-2222-4222-8222-222222222222",
          name: "Python",
          slug: "python",
          kind: "language",
          status: "active",
        },
      ],
      total: 1,
    };
    expect(parseAudienceTopics(page)).toEqual(page);
    expect(() => parseAudienceTopics({ items: page.items })).toThrow();
    expect(() =>
      parseAudienceTopics({ ...page, items: [{ ...page.items[0], id: "invalid" }] }),
    ).toThrow();
  });
});

it("generates deterministically twice without changing files outside generated output", async () => {
  const workspace = await mkdtemp(path.join(root, "apps", ".partner-contract-check-"));
  try {
    for (const name of ["orval.config.ts", "openapi.json", "package.json"]) {
      await cp(path.join(root, "apps/partner", name), path.join(workspace, name));
    }
    const sentinels = [
      "src/app/keep.ts",
      "src/app/api/v1/other/route.ts",
      "src/app/api/v1/partner/custom/keep.ts",
      "node_modules/keep.js",
      "README.md",
    ];
    for (const name of sentinels) {
      await mkdir(path.dirname(path.join(workspace, name)), { recursive: true });
      await writeFile(path.join(workspace, name), "Preserve outside generated output\n");
    }
    const preserved = await Promise.all(
      [...sentinels, "orval.config.ts", "openapi.json", "package.json"].map(
        async (name) => [name, await readFile(path.join(workspace, name), "utf8")] as const,
      ),
    );
    async function generatedContents(directory: string, routesOnly = false) {
      const entries = await readdir(directory, { recursive: true, withFileTypes: true });
      return Object.fromEntries(
        await Promise.all(
          entries
            .filter((entry) => entry.isFile() && (!routesOnly || entry.name === "route.ts"))
            .map(
              async (entry) =>
                [
                  path.relative(directory, path.join(entry.parentPath, entry.name)),
                  await readFile(path.join(entry.parentPath, entry.name), "utf8"),
                ] as const,
            ),
        ),
      );
    }
    let first;
    for (let pass = 0; pass < 2; pass++) {
      await execute(
        process.execPath,
        [
          path.join(root, "node_modules/orval/dist/bin/orval.mjs"),
          "--config",
          path.join(workspace, "orval.config.ts"),
        ],
        { cwd: workspace },
      );
      await generatePartnerRoutes({
        source: pathToFileURL(path.join(workspace, "openapi.json")),
        target: pathToFileURL(path.join(workspace, "src/lib/api/generated/partner-operations.ts")),
        routesRoot: pathToFileURL(path.join(workspace, "src/app/api/v1/partner")),
      });
      for (const [name, content] of preserved) {
        expect(await readFile(path.join(workspace, name), "utf8")).toBe(content);
      }
      const generated = {
        sdk: await generatedContents(path.join(workspace, "src/lib/api/generated")),
        routes: await generatedContents(path.join(workspace, "src/app/api/v1/partner"), true),
      };
      expect(generated.sdk).not.toHaveProperty("models-only.ts");
      expect(generated.sdk["models/accountOut.ts"]).toContain("export interface AccountOut");
      expect(generated.sdk["validators.ts"]).toContain("PartnerAccountPreferencesUpdateResponse");
      expect(generated.sdk["validators.ts"]).toContain("PartnerConnectionPreviewResponse");
      expect(generated.sdk["validators.ts"]).toContain("PartnerConnectorResponseResponse");
      expect(generated.sdk["partner-operations.ts"]).toContain("partnerGatewayOperations");
      expect(generated.routes["accounts/[account_id]/products/[product_id]/route.ts"]).toContain(
        "bindPartnerOperation",
      );
      if (pass === 0) {
        first = generated;
        expect(generated.sdk).toEqual(
          await generatedContents(path.join(root, "apps/partner/src/lib/api/generated")),
        );
        expect(generated.routes).toEqual(
          await generatedContents(path.join(root, "apps/partner/src/app/api/v1/partner"), true),
        );
      } else expect(generated).toEqual(first);
    }
  } finally {
    await rm(workspace, { recursive: true, force: true });
  }
}, 20_000);
