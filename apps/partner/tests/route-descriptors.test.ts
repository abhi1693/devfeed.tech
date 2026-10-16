import { describe, expect, it } from "vitest";
import { resolvePageRoute } from "../../../packages/telemetry/src/page-routes";
import { pageRouteCatalogs } from "../../../packages/telemetry/src/page-routes.generated";
import {
  accountSettingsPath,
  accountSettingsSections,
  portalPath,
  productPath,
} from "../src/lib/routes";

const name = (pathname: string) => resolvePageRoute(pathname, pageRouteCatalogs.partner);

describe("partner route descriptor telemetry", () => {
  it.each(["overview", "performance", "products", "connections", "settings"] as const)(
    "labels the %s portal page",
    (section) => {
      const path = portalPath(section);
      expect(name(path)).toBe(path);
    },
  );

  it.each([
    "private-customer-product",
    "private%40example.com",
    "22222222-2222-4222-8222-222222222222",
  ])("projects product forms without exposing %s", (productId) => {
    const paths = [
      [productPath(productId), "/products/:id"],
      [productPath(productId, { edit: true }), "/products/:id/placements"],
      [productPath(productId, { action: "edit" }), "/products/:id/edit"],
      [productPath(productId, { action: "archive" }), "/products/:id/archive"],
    ];
    for (const [path, expected] of paths) expect(name(path)).toBe(expected);
  });

  it("uses only actual account settings tabs and hides the selected account", () => {
    expect(name(accountSettingsPath("private-account"))).toBe("/settings/:id");
    for (const section of accountSettingsSections)
      expect(name(accountSettingsPath("private-account", section))).toBe(
        `/settings/:id/${section}`,
      );
    expect(name("/settings/private-account/unknown")).toBeNull();
    expect(name("/settings/private-account/general")).toBeNull();
  });

  it.each([
    ["/connections/new", "/connections/new"],
    ["/connections/private-provider", "/connections/:id"],
    ["/connections/private-provider/edit", "/connections/:id/edit"],
    ["/connections/private-provider/disconnect", "/connections/:id/disconnect"],
    [
      "/api/v1/partner/accounts/private-account/products/private-product",
      "/v1/partner/accounts/:id/products/:id",
    ],
    [
      "/v1/partner/accounts/private-account/connections/private-provider/connector-preview",
      "/v1/partner/accounts/:id/connections/:id/connector-preview",
    ],
  ])("labels supported route %s", (path, expected) => expect(name(path)).toBe(expected));

  it.each([
    "/partnerships/accounts/private-account",
    "/api/v1/admin/partnerships/accounts",
    "/settings/private-account/audience/extra",
    "/connections/private-provider/unknown",
    "/products/private-product/unknown",
    "/api/v1/partner/accounts/private-account/unknown",
  ])("rejects unregistered route %s", (pathname) => expect(name(pathname)).toBeNull());
});
