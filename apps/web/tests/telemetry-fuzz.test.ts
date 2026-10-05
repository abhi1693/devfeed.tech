import fc from "fast-check";
import { describe, expect, it } from "vitest";
import { methodName, normalizeMeta, routeName } from "@devfeed/telemetry/privacy";
import { propertyOptions } from "../../../scripts/ci/property-config.mjs";

const options = propertyOptions();

describe("telemetry privacy properties", () => {
  it("keeps authentication query values and fragments out of metric route labels", () => {
    fc.assert(
      fc.property(
        fc.constantFrom("/login", "/register", "/my-feed", "/settings/profile"),
        fc.string(),
        fc.string(),
        fc.string(),
        (path, code, state, fragment) => {
          const url = new URL(path, "https://devfeed.tech");
          url.searchParams.set("code", code);
          url.searchParams.set("state", state);
          url.searchParams.set("access_token", code);
          url.hash = fragment;
          expect(routeName(url.href)).toBe(path);
        },
      ),
      options,
    );
  });

  it("collapses arbitrary private identifiers into stable route labels", () => {
    fc.assert(
      fc.property(
        fc.constantFrom(
          ["/articles/", "/articles/:id"],
          ["/sources/", "/sources/:id"],
          ["/users/", "/users/:id"],
          ["/content/articles/", "/content/articles/:id"],
          ["/api/v1/user/account/", "/v1/user/account/:path"],
        ),
        fc.string(),
        ([prefix, label], identifier) => {
          const url = `${prefix}private-${encodeURIComponent(identifier)}`;
          expect(routeName(url)).toBe(label);
        },
      ),
      options,
    );
  });

  it("bounds metric cardinality for malformed and unexpected inputs", () => {
    fc.assert(
      fc.property(fc.anything(), (input) => {
        const label = routeName(input);
        expect(label.length).toBeLessThan(100);
        expect(label).not.toMatch(/[?#@%\\\s]/);
        expect(["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "OTHER"]).toContain(
          methodName(input),
        );
      }),
      options,
    );
  });

  it("uses trusted application metadata and removes client sampling decisions", () => {
    fc.assert(
      fc.property(fc.jsonValue(), fc.jsonValue(), (app, isSampled) => {
        const input = { app, session: { attributes: { isSampled, marker: "retained" } } };
        const settings = {
          enabled: true,
          app: "web" as const,
          version: "0.0.50",
          environment: "production",
        };
        const normalized = normalizeMeta(input, settings);
        expect(normalized.app).toMatchObject({
          name: "devfeed-web",
          version: settings.version,
          environment: settings.environment,
        });
        expect(normalized.session?.attributes).toEqual({ marker: "retained" });
        expect(input.session.attributes.isSampled).toEqual(isSampled);
      }),
      options,
    );
  });
});
