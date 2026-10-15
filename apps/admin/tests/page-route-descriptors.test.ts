import { describe, expect, it } from "vitest";
import { resolvePageRoute } from "../../../packages/telemetry/src/page-routes";
import { pageRouteCatalogs } from "../../../packages/telemetry/src/page-routes.generated";
import { adminTelemetryDefinitions } from "@/lib/routes";
import { accountsPath, partnerAccountSections } from "@/lib/partner-account-routes";
import { partnerDetailSections, resolvePartnerDetailSection } from "@/lib/partner-detail-routes";

const name = (pathname: string) => resolvePageRoute(pathname, pageRouteCatalogs.admin);

describe("admin route descriptor telemetry", () => {
  it.each(adminTelemetryDefinitions())(
    "projects the rendered resource route $route",
    ({ route, label }) => {
      expect(name(route.replaceAll("[id]", "private-record-42"))).toBe(label);
    },
  );

  it.each(partnerAccountSections)(
    "shares account section $segments with its router",
    ({ segments }) => {
      const suffix = segments
        .map((segment) => (segment === ":identifier" ? "campaign%3Aprivately-named" : segment))
        .join("/");
      const path = `${accountsPath}/private-account${suffix ? `/${suffix}` : ""}`;
      expect(name(path)).toBe(
        `${accountsPath}/:id${suffix ? `/${segments.map((segment) => (segment === ":identifier" ? ":id" : segment)).join("/")}` : ""}`,
      );
    },
  );

  it.each(Object.entries(partnerDetailSections))(
    "allows only rendered %s detail sections",
    (resource, sections) => {
      const base = `/partnerships/${resource}/private-run`;
      expect(name(base)).toBe(`/partnerships/${resource}/:id`);
      for (const section of sections) {
        expect(name(`${base}/${section}`)).toBe(`/partnerships/${resource}/:id/${section}`);
        expect(
          resolvePartnerDetailSection(resource as keyof typeof partnerDetailSections, [section]),
        ).toBe(section);
      }
      expect(name(`${base}/unknown`)).toBeNull();
      expect(name(`${base}/logs/extra`)).toBeNull();
      expect(
        resolvePartnerDetailSection(resource as keyof typeof partnerDetailSections, ["unknown"]),
      ).toBeUndefined();
    },
  );

  it.each([
    ["/partnerships/connections/private-provider/edit", "/partnerships/connections/:id/edit"],
    ["/jobs/analysis/topics/private-run/logs", "/jobs/analysis/topics/:id/logs"],
    [
      "/taxonomy/relationships/private-topic/uses_language/private-language/related",
      "/taxonomy/relationships/:id/:type/:id/related",
    ],
    ["/users/private-user/bookmarks", "/users/:id/bookmarks"],
    ["/users/private-user/reading-days", "/users/:id/reading-days"],
    [
      "/api/v1/admin/partner-accounts/private-account/assets/private-asset",
      "/v1/admin/partner-accounts/:id/assets/:id",
    ],
    ["/settings/defaults", "/settings/defaults"],
  ])("hides identifiers in %s", (pathname, expected) => expect(name(pathname)).toBe(expected));

  it.each([
    "/settings/private-section",
    "/partnerships/accounts/private-account/assets/private-asset/view",
    "/partnerships/accounts/private-account/unknown",
    "/api/v1/admin/partnerships/private-operation",
    "/api/v1/partner/accounts/private-account/products",
    "/products/private-product/edit",
    "/taxonomy/relationships/a/unknown/b",
  ])("rejects unregistered route %s", (pathname) => expect(name(pathname)).toBeNull());
});

describe("shared template matching", () => {
  const templates = [
    { path: "/products/:id", label: "/products/:id" },
    { path: "/products/new", label: "/products/new" },
    { path: "/products/:id/edit", label: "/products/:id/edit" },
    { path: "/md/:*path", label: "/md/:path" },
  ];

  it("gives concrete routes priority independently of declaration order", () => {
    expect(resolvePageRoute("/products/new", templates)).toBe("/products/new");
    expect(resolvePageRoute("/products/private%40example.com/edit/", templates)).toBe(
      "/products/:id/edit",
    );
    expect(resolvePageRoute("/md/topics/private-name", templates)).toBe("/md/:path");
    expect(resolvePageRoute("/md", templates)).toBeNull();
    expect(resolvePageRoute("/products/private/unsupported", templates)).toBeNull();
  });

  it.each([
    "products/private",
    "/products//",
    "/products/%2F",
    "/products/%5C",
    "/products/%00",
    "/products/%ZZ",
    "/products/private?token=secret",
    "/products/private#secret",
  ])("rejects ambiguous input %s", (pathname) =>
    expect(resolvePageRoute(pathname, templates)).toBeNull(),
  );
});
