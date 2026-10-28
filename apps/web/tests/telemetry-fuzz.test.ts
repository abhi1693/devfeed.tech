import fc from "fast-check";
import { describe, expect, it } from "vitest";
import { methodName, routeName } from "@devfeed/telemetry/privacy";
import { propertyOptions } from "../../../scripts/ci/property-config.mjs";

const options = propertyOptions();

describe("telemetry privacy properties", () => {
  it("keeps authentication query values and fragments out of metric route labels", () => {
    fc.assert(
      fc.property(
        fc.constantFrom("/login", "/register", "/mcp/authorize", "/settings/profile"),
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
          ["/articles/", "/articles/:id", "web"],
          ["/sources/", "/sources/:id", "web"],
          ["/users/", "/users/:username", "web"],
          ["/content/articles/", "/content/articles/:id", "admin"],
          ["/products/", "/products/:id", "partner"],
        ),
        fc
          .array(fc.constantFrom(..."abcdefghijklmnopqrstuvwxyz0123456789-"), {
            maxLength: 100,
          })
          .map((parts) => parts.join("")),
        ([prefix, label, app], identifier) => {
          const url = `${prefix}private-${encodeURIComponent(identifier)}`;
          expect(routeName(url, app as "web" | "admin" | "partner")).toBe(label);
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
});
